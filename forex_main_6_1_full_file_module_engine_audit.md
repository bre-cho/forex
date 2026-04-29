# FOREX-MAIN-6(1) — FULL FILE / MODULE / ENGINE AUDIT

**Mục tiêu audit:** nghiên cứu code theo chuẩn hệ thống **live trading thật** cho forex/crypto: broker thật, order lifecycle thật, risk fail-closed, daily TP/loss lock, reconciliation, operator UI, CI safety.  
**Kết luận ngắn:** repo đã tiến bộ mạnh so với các bản trước, có nhiều cấu phần production như order ledger, idempotency reservation, broker receipt contract, unknown order queue, reconciliation daemon, daily lock runtime, live start preflight, frozen gate context. Tuy nhiên **vẫn chưa đạt chuẩn chạy tiền thật không giám sát** vì còn nhiều điểm P0 liên quan đến tính nguyên tử của order ledger, split live/demo provider, broker-native risk, legacy isolation, atomic daily lock, reconciliation thực sự với broker và contract test end-to-end.

> Không có hệ bot nào có thể cam kết lợi nhuận 500% an toàn. Mục tiêu hoàn thiện code phải là: giảm tail-risk, khóa lỗi vận hành, kiểm soát drawdown, audit đầy đủ, fail-closed khi không chắc chắn.

---

## 1. Tổng quan cấu trúc repo

Repo có **363 file**. Cấu trúc chính:

| Khu vực | Vai trò | Nhận xét |
|---|---|---|
| `apps/api` | FastAPI control plane, DB models, routers, runtime registry, live trading API | Đây là API chính nên giữ làm source-of-truth. |
| `apps/web` | Next.js user/operator frontend | Có các trang live orders, live control center, runtime control, trading brain. |
| `apps/admin` | Admin dashboard | Có broker health, operations dashboard, runtime/users/workspaces. |
| `services/trading-core` | Brain/runtime/risk/strategy engines | Đây là core execution runtime. Nhiều engine đã được migrate từ legacy. |
| `services/execution-service` | Broker provider, order routing, receipt, reconciliation | Đây là lớp “tay” đặt lệnh thật. Cần harden cao nhất. |
| `services/signal-service` | Signal builder/feed/scoring | Cần thêm signal lifecycle + schema versioning. |
| `services/analytics-service` | Drawdown/equity/expectancy/PF/sharpe | Tốt cho reporting, chưa đủ realtime risk. |
| `services/notification-service` | Telegram/email/discord/webhook | Cần nối sự kiện critical thật. |
| `ai_trading_brain` | AI brain contract/runtime/governance/memory | Có kiến trúc tốt nhưng cần hard boundary với live execution. |
| `backend/`, `frontend/` | Legacy stack | Vẫn còn nhiều file lớn, duplicated engine. Phải khóa khỏi production. |
| `.github/scripts` | Verification scripts | Có nhiều guard tốt nhưng cần nối thành release gate bắt buộc. |

---

## 2. Kết luận production readiness

### Đã có tiến bộ lớn

- Có `orders`, `order_idempotency_reservations`, `broker_order_attempts`, `order_state_transitions`, `broker_execution_receipts`, `reconciliation_queue_items`, `daily_trading_state` trong model/migration.
- `ExecutionEngine` đã bắt buộc `ExecutionCommand` khi live, có idempotency verifier, frozen gate hash, provider capability, receipt contract.
- `BotRuntime` có live reconciliation worker và chặn provider `stub/degraded/unavailable/paper` trong live.
- `LiveStartPreflight` đã sync broker equity, chặn daily lock, unresolved unknown orders, critical incidents.
- `reconciliation_daemon.py` chạy độc lập ở FastAPI lifespan.
- Frontend đã có panel live readiness, daily lock, unknown orders, execution receipt.
- CI scripts có các guard như no live stub, live import boundary, broker gate wiring, production no legacy stack.

### Chưa đủ để chạy tiền thật không giám sát

Các rủi ro P0 còn lại:

1. **Order lifecycle chưa bảo đảm atomic exactly-once hoàn chỉnh.** `mark_submitting_hook` nếu fail chỉ warning, nhưng ở live tiền thật phải fail-closed trước khi gọi broker.
2. **Provider live identity chưa đủ cứng.** `CTraderProvider` còn fallback/demo path, `mode` có thể là `stub`, `paper`, `live`, nhưng contract live broker cần tách class rõ `CTraderLiveProvider` và `CTraderDemoProvider`.
3. **Unknown order reconciliation chưa chứng minh truy vấn broker thật đầy đủ.** Queue/daemon có, nhưng cần worker thực hiện broker query bằng `client_order_id`, `broker_order_id`, deal/position và cập nhật ledger.
4. **Risk context còn fallback trong runtime.** `RiskContextBuilder` cho paper có fallback spec là đúng, nhưng live phải hard fail nếu broker không trả instrument spec / margin / pip value / tick size.
5. **Daily TP/loss lock chưa có exactly-once global lock orchestration.** Có state/service/controller, nhưng cần advisory lock/row lock + kill all positions + cancel pending + broadcast operator event.
6. **Legacy stack vẫn được include route.** `apps/api/app/main.py` include `legacy.router`; `backend/` vẫn chứa engine duplicated. Production phải chặn import/run hoàn toàn.
7. **Main loop còn quá nhiều responsibility trong `BotRuntime` 1510 dòng.** Khó audit, dễ phát sinh side effect khi live.
8. **Tests hiện có nhiều nhưng chưa đủ full broker contract E2E.** Cần test crash-after-submit, broker timeout, duplicate order, recon resolution, daily TP hit, broker disconnect, stale quote.

---

## 3. Audit theo từng module / engine quan trọng

## 3.1 `apps/api` — Control Plane

### `apps/api/app/main.py`

**Hiện trạng**
- Boot `RuntimeRegistry`.
- Warm Redis.
- Start `reconciliation_daemon` trong lifespan.
- Include nhiều routers, bao gồm `legacy.router`.

**Vấn đề**
- `reconciliation_daemon` chạy luôn mọi env. Cần feature flag theo `ENABLE_RECONCILIATION_DAEMON=true` và health endpoint riêng.
- `legacy.router` vẫn được include trong API chính. Đây là production risk.
- Health endpoint mới trả runtime count, chưa trả Postgres/Redis/Broker/Daemon/Migration head status.

**Đề xuất hoàn thiện**
- Tạo `settings.enable_legacy_routes=false`; production hard-block nếu true.
- `/health/live`, `/health/ready`, `/health/deep` tách riêng.
- `/health/deep` kiểm tra DB, Redis, reconciliation daemon, migration head, broker provider registry.
- Daemon phải có heartbeat table `worker_heartbeats` hoặc Redis key TTL.

---

### `apps/api/app/models/__init__.py`

**Hiện trạng tốt**
- Có schema khá đầy đủ: `Order`, `Trade`, `TradingDecisionLedger`, `PreExecutionGateEvent`, `OrderIdempotencyReservation`, `DailyTradingState`, `BrokerOrderAttempt`, `OrderStateTransition`, `BrokerExecutionReceipt`, `BrokerReconciliationRun`, `ReconciliationQueueItem`.
- `Order` có unique `(bot_instance_id, idempotency_key)`.

**Vấn đề**
- `Order` vừa có `status`, vừa có `current_state`, `submit_status`, `fill_status`, `reconciliation_status`. Nếu không có state machine enforced ở DB/service, trạng thái có thể lệch.
- `broker_order_id` nullable là đúng lúc pending/unknown, nhưng live filled/acked cần constraint mềm qua service/test.
- `Trade` vẫn tách khỏi `Order` nhưng chưa thấy invariant rõ: một order filled tạo trade như thế nào, partial fill cập nhật ra sao.

**Đề xuất**
- Chọn `current_state` là source-of-truth, các field status khác là projection.
- Thêm invariant service:
  - `RESERVED -> SUBMITTING -> ACKED -> PARTIAL/FILLED/REJECTED/UNKNOWN -> RECONCILED/DEAD_LETTER`
  - Không cho nhảy trạng thái bất hợp lệ.
- Thêm DB indexes:
  - `(bot_instance_id, current_state)`
  - `(bot_instance_id, broker_order_id)`
  - `(bot_instance_id, broker_position_id)`
  - `(bot_instance_id, created_at)`
- Thêm `order_events` append-only để audit raw broker callbacks.

---

### `apps/api/app/services/order_ledger_service.py`

**Hiện trạng tốt**
- Có `record_order_lifecycle_event()` xử lý `order_submitted`, `order_filled`, `order_rejected`, `order_unknown`.
- Có hash raw response.
- Có enqueue unknown order.

**Vấn đề P0**
- Cần đảm bảo **one DB transaction** cho: reservation -> attempt -> order -> transition -> receipt -> projection -> queue. Nếu các method commit riêng, crash giữa chừng vẫn gây lệch source-of-truth.
- Nếu `mark_submitting_hook` fail trong `ExecutionEngine`, engine hiện chỉ warning và vẫn có thể gọi broker. Đây là rủi ro duplicate/ghost order.

**Đề xuất**
- Tạo `OrderLifecycleUnitOfWork`:
  - `reserve_intent()`
  - `mark_submitting_atomic()`
  - `persist_broker_result_atomic()`
  - `enqueue_unknown_atomic()`
- Trong live mode: **không được gọi broker nếu `mark_submitting_atomic` fail**.
- Dùng `SELECT FOR UPDATE` trên reservation/order row.
- Mọi lifecycle event ghi `correlation_id`, `brain_cycle_id`, `gate_context_hash`, `request_hash`, `policy_snapshot_hash`.

---

### `apps/api/app/services/live_start_preflight.py`

**Hiện trạng tốt**
- Sync broker equity trước khi start live.
- Fail-closed nếu broker equity invalid.
- Block daily lock, unresolved unknown orders, critical incident.

**Thiếu**
- Chưa đủ broker capability proof: client order id support, account id match, market open, symbol tradable, min/max volume, margin estimate, clock skew, quote freshness.
- Chưa kiểm tra daemon heartbeat.
- Chưa kiểm tra policy approval version/hash có khớp runtime.

**Đề xuất P0**
- `LiveStartPreflight` phải trả checklist bắt buộc:
  - `broker_connected`
  - `account_id_verified`
  - `capability_proof_passed`
  - `client_order_id_supported`
  - `symbol_specs_loaded`
  - `quote_fresh`
  - `margin_estimation_ok`
  - `daily_state_fresh`
  - `daily_lock_inactive`
  - `unknown_orders_zero`
  - `critical_incidents_zero`
  - `reconciliation_daemon_heartbeat_ok`
  - `policy_approved_hash_match`
- Nếu bất kỳ check nào false → không start live.

---

### `apps/api/app/services/daily_lock_runtime_controller.py` + `daily_profit_lock_engine.py` + `daily_trading_state.py`

**Hiện trạng**
- Có daily state, daily profit amount, daily loss pct, locked, lock_reason.
- Có runtime controller và test.

**Vấn đề**
- Daily TP/loss lock cần chạy như **global safety orchestrator**, không chỉ evaluator.
- Khi hit TP/loss phải đóng vòng:
  1. set lock DB exactly-once,
  2. stop runtime,
  3. cancel pending orders,
  4. close positions nếu policy yêu cầu,
  5. tạo incident/info event,
  6. push UI notification,
  7. block restart đến ngày giao dịch mới hoặc operator override.

**Đề xuất P0**
- Tạo `DailyLockOrchestrator`:
  - `evaluate_and_lock(bot_id, broker_equity, realized_pnl, unrealized_pnl)`
  - `apply_lock_actions(bot_id, action_set)`
  - `unlock_next_trading_day(bot_id)`
- Thêm bảng `daily_lock_actions` đã có migration 0018; cần enforce lifecycle:
  - `requested -> executing -> succeeded/failed_needs_operator`
- Thêm advisory lock theo `(bot_id, trading_day)`.

---

### `apps/api/app/workers/reconciliation_daemon.py`

**Hiện trạng tốt**
- Daemon độc lập polling queue.
- Retry sau 30s, deadline 5 phút hoặc 3 fail → dead letter + critical incident + daily lock.
- Có lease service để tránh nhiều worker xử lý cùng item.

**Vấn đề P0**
- Daemon hiện chủ yếu xử lý queue status/escalation. Cần chứng minh nó gọi broker provider để tìm order thật theo `client_order_id`/`broker_order_id`/execution id.
- `_lock_bot_daily_state` chỉ lock nếu daily state row tồn tại. Nếu chưa tồn tại thì không tạo row mới → bot có thể không bị lock như kỳ vọng.
- Incident có thể duplicate nếu daemon retry cùng dead-letter nhiều lần, cần idempotent incident key.

**Đề xuất**
- `UnknownOrderReconciliationWorker` phải:
  - query broker by client order id,
  - query executions/deals by client id,
  - query open positions by symbol/account,
  - nếu found → persist receipt + projection + mark resolved,
  - nếu not found but provider proves no order → mark rejected/cancelled,
  - nếu provider uncertain → retry/escalate.
- `_lock_bot_daily_state` phải upsert daily state.
- `TradingIncident` cần `dedupe_key` unique.

---

## 3.2 `services/execution-service` — Broker / Execution Layer

### `execution_engine.py`

**Hiện trạng tốt**
- Live mode bắt buộc `ExecutionCommand`.
- Kiểm tra provider supports client order id.
- Kiểm tra `brain_cycle_id`, `idempotency_key`, pre-execution context, reservation verifier.
- Kiểm tra frozen gate context hash và binding request/context/provider.
- Timeout broker submit → `submit_status=UNKNOWN` và enqueue unknown.
- Enforce live receipt contract: `ACKED`, `FILLED/PARTIAL`, broker id/position id, account id, raw response hash.

**Vấn đề P0**
- `mark_submitting_hook` failure chỉ `logger.warning`, không block broker submit. Live mode phải fail-closed.
- `enqueue_unknown_hook` failure sau timeout cũng chỉ warning; nếu queue không ghi được thì ghost order không được theo dõi.
- `_signal_id` lấy từ `payload.intent` theo cách fragile; nên field hóa trong `ExecutionCommand`.
- Receipt contract đang coi success phải có filled/partial; một broker có thể ACKED nhưng chưa filled ngay. Cần phân biệt market order vs limit order.

**Đề xuất**
- Live submit flow chuẩn:
  1. `reserve idempotency` trước đó ở runtime/API.
  2. `mark_submitting_atomic` bắt buộc thành công.
  3. gọi broker với client order id.
  4. nếu timeout/exception → `UNKNOWN` và enqueue unknown bắt buộc thành công; nếu enqueue fail → system critical lock.
  5. broker result → persist receipt atomic.
- Receipt states:
  - `SUBMIT_ACKED`, `SUBMIT_REJECTED`, `FILL_PENDING`, `PARTIAL_FILLED`, `FILLED`, `UNKNOWN`.
- Không ép limit/pending order phải `FILLED` ngay.

---

### `providers/base.py`

**Hiện trạng**
- Có `BrokerProvider`, `OrderRequest`, `OrderResult`, `PreExecutionContext`, capability proof.
- Optional methods live: `get_instrument_spec`, `estimate_margin`, `get_order_by_client_id`, `get_executions_by_client_id`, `close_all_positions`.

**Thiếu**
- Live provider contract chưa có `cancel_all_pending_orders`, `get_open_positions`, `get_server_time`, `get_symbol_status`, `get_quote` chuẩn hóa.
- `OrderResult` cần chuẩn hóa broker timestamps, request id, error code, raw response hash, normalized status.

**Đề xuất**
- Tách interface:
  - `MarketDataProvider`
  - `ExecutionProvider`
  - `AccountProvider`
  - `ReconciliationProvider`
- Live provider phải implement toàn bộ, không optional.
- `supports_client_order_id` là required hard check.

---

### `providers/ctrader.py`, `ctrader_live.py`, adapters

**Hiện trạng**
- Có wrapper `CTraderProvider` và adapter tách execution/market data.
- Có fail-closed khi live mà engine unavailable.
- Có `CTraderLiveProvider` file riêng.

**Vấn đề**
- `ctrader.py` vẫn có comment fallback demo/paper, có mode `stub`, và logic live/demo trộn.
- Production cần cấm dùng `CTraderProvider(live=True)` nếu adapter chưa chứng minh Open API capabilities.
- Cần test thật với cTrader sandbox/demo trước khi live.

**Đề xuất P0**
- Rename:
  - `CTraderDemoProvider`
  - `CTraderLiveProvider`
- `CTraderProvider` legacy wrapper chỉ dùng paper/demo, không expose live.
- Live provider phải có startup proof:
  - token valid,
  - account id match,
  - account currency,
  - symbol mapping,
  - volume step/min/max,
  - quote stream ok,
  - order client id echo ok nếu broker hỗ trợ.

---

### `unknown_order_reconciler.py` + `reconciliation_worker.py`

**Hiện trạng**
- Có worker/reconciler tests.

**Cần hoàn thiện**
- Reconciler cần idempotent update vào order ledger.
- Khi broker trả partial fill, phải update `filled_volume`, `avg_fill_price`, trade projection.
- Khi broker trả không tìm thấy order, không được vội reject nếu broker API không đảm bảo query complete.

**Đề xuất**
- Decision matrix:
  - `FOUND_FILLED` → fill order + trade.
  - `FOUND_PARTIAL` → partial state + keep recon open.
  - `FOUND_REJECTED` → reject.
  - `NOT_FOUND_BROKER_CERTAIN` → cancelled/rejected.
  - `NOT_FOUND_UNCERTAIN` → retry.
  - `BROKER_DOWN` → retry, do not mutate order.

---

## 3.3 `services/trading-core` — Runtime / Risk / Strategy Engine

### `runtime/bot_runtime.py`

**Hiện trạng tốt**
- Là runtime trung tâm: init engines, start/stop, run loop, signal execution, reconciliation, snapshot.
- Live mode có provider usability check và reconciliation worker.
- Có nhiều hooks nối DB/API.

**Vấn đề kiến trúc**
- File 1510 dòng, quá nhiều trách nhiệm: signal, brain, risk, execution, reconciliation, daily state, snapshot.
- Có fallback logic instrument spec trong runtime; live phải tuyệt đối không dùng fallback.
- Dễ xảy ra duplicate execution path nếu signal coordinator callback và brain pipeline không được khóa rõ.

**Đề xuất refactor**
- Tách thành 6 engine nhỏ:
  - `RuntimeLifecycleManager`
  - `SignalIngestionEngine`
  - `BrainDecisionRunner`
  - `RiskPreflightRunner`
  - `ExecutionCommandBuilder`
  - `RuntimeReconciliationSupervisor`
- Live invariant:
  - Một signal chỉ có một `brain_cycle_id`.
  - Một cycle chỉ tạo một `idempotency_key`.
  - Một idempotency key chỉ có một order lifecycle.

---

### `runtime/pre_execution_gate.py`

**Hiện trạng tốt**
- Gate đánh giá context, chặn live stub/degraded/unavailable provider.
- Có test market data quality, daily loss/profit, confidence, RR, spread, open positions.

**Cần hoàn thiện**
- Gate context cần cố định schema/version.
- Cần đưa thêm:
  - `account_id`, `broker`, `symbol`, `side`, `volume`, `instrument_spec_hash`, `policy_hash`, `quote_id`, `quote_timestamp`, `margin_required`, `free_margin_after_trade`, `portfolio_exposure_after_trade`.

**Đề xuất**
- Tạo `GateContextV1` pydantic/dataclass typed.
- `hash_gate_context()` phải hash canonical JSON của context typed.
- Mọi order submit phải bind request với context: symbol/side/volume/broker/account/policy/quote.

---

### `runtime/frozen_context_contract.py`

**Hiện trạng tốt**
- Có validate binding giữa request, context và provider.

**Đề xuất**
- Bắt buộc compare:
  - `request.symbol == context.symbol`
  - `request.side == context.side`
  - `request.volume == context.approved_volume`
  - `provider_name == context.broker`
  - `account_id == context.account_id`
  - `idempotency_key == context.idempotency_key`
  - `policy_hash == active_policy_hash`

---

### `risk/risk_context_builder.py`, `broker_native_risk_context.py`, `instrument_spec.py`, `position_sizing.py`

**Hiện trạng tốt**
- Có broker-native risk context.
- Fallback spec được ghi rõ chỉ dành cho paper/backtest.
- Có test instrument spec và risk builder.

**Vấn đề**
- Live risk phải dựa broker-native 100%: instrument spec, pip value, margin estimate, account leverage, commission/swap/spread.
- Risk sizing phải tính theo equity/free margin thật, không chỉ config.

**Đề xuất P0**
- `RiskContextBuilder(runtime_mode='live')` phải fail nếu thiếu broker spec/margin/quote.
- Thêm `RiskPreflightResult`:
  - `requested_risk_amount`
  - `position_size`
  - `pip_value`
  - `stop_distance`
  - `margin_required`
  - `free_margin_after`
  - `symbol_exposure_after`
  - `portfolio_var_proxy`
  - `allowed/max_reason`
- Add tests cho XAUUSD, BTCUSD, JPY pairs, crypto lot precision.

---

### Strategy/AI engines trong `trading_core/engines/*`

Các file lớn như:
- `wave_detector.py`
- `signal_coordinator.py`
- `decision_engine.py`
- `entry_logic.py`
- `risk_manager.py`
- `trade_manager.py`
- `capital_manager.py`
- `auto_pilot.py`
- `performance_tracker.py`
- `adaptive_controller.py`
- `causal_strategy_engine.py`
- `game_theory_engine.py`
- `meta_learning_engine.py`
- `self_play_engine.py`
- `sovereign_oversight_engine.py`
- `autonomous_enterprise_engine.py`

**Nhận xét**
- Nhiều engine thiên về “AI/strategy intelligence”, tốt cho paper/backtest/research.
- Với live trading, các engine này **không được trực tiếp đặt lệnh**. Chỉ được tạo signal/decision proposal.
- Execution phải đi qua `BrainDecisionLedger -> PreExecutionGate -> OrderLedger -> ExecutionEngine`.

**Đề xuất**
- Gắn nhãn engine:
  - `RESEARCH_ONLY`
  - `PAPER_ALLOWED`
  - `LIVE_SIGNAL_ONLY`
  - `LIVE_EXECUTION_ALLOWED` — chỉ execution/risk/order layer được quyền này.
- Tạo `EngineCapabilityRegistry` và CI check: không engine strategy nào import broker provider trực tiếp.

---

## 3.4 `ai_trading_brain`

### `brain_runtime.py`, `decision_engine.py`, `governance.py`, `memory_engine.py`, `unified_trade_pipeline.py`

**Hiện trạng**
- Có brain contracts, memory/evolution/governance.
- Có unified trade pipeline.

**Cần khóa an toàn**
- AI brain không được bypass risk/order ledger.
- Memory/evolution không được tự tăng risk/live policy nếu chưa có approval.
- Mọi quyết định live phải được snapshot vào `TradingDecisionLedger`.

**Đề xuất**
- Brain output chỉ là:
  - `ALLOW_SIGNAL`, `BLOCK_SIGNAL`, `REDUCE_SIZE`, `WAIT`, `CLOSE_ONLY`
- Brain không được trả `place_order` trực tiếp.
- Add `PolicyApprovalControlPlane` bắt buộc nếu brain muốn thay đổi risk.

---

## 3.5 `apps/web` + `apps/admin`

**Hiện trạng tốt**
- Có UI cho:
  - live control center,
  - live orders,
  - runtime control,
  - broker connections,
  - trading brain,
  - daily lock panel,
  - readiness panel,
  - receipt drawer,
  - reconciliation timeline,
  - unknown orders panel.

**Thiếu cho operator live thật**
- Nút emergency stop toàn account.
- “Close all positions” có xác nhận 2 bước.
- Reconciliation queue manual resolve/escalate.
- Broker health realtime + quote freshness.
- Daily TP/loss policy editor có approval flow.
- Audit trail viewer theo bot/order/signal.

**Đề xuất**
- Trang `/live-control-center` phải hiển thị 7 đèn:
  - broker connected,
  - quote fresh,
  - daily lock,
  - unknown orders,
  - open critical incidents,
  - policy approved,
  - daemon heartbeat.
- Không cho bấm “Start Live” nếu readiness chưa pass.

---

## 3.6 `backend/` và `frontend/` legacy

**Hiện trạng**
- `backend/main.py` 3062 dòng.
- `backend/engine/*` duplicate với `services/trading-core/trading_core/engines/*`.
- `backend/README_DEPRECATED.md` có đánh dấu deprecated.

**Rủi ro**
- Nếu Docker/CI/import path trỏ nhầm sang legacy, live mode có thể chạy code cũ.
- Duplicate engine gây drift logic.

**Đề xuất P0**
- Production image không copy `backend/` và `frontend/` legacy.
- `verify_production_no_legacy_stack.sh` phải là required status check.
- `apps/api/app/routers/legacy.py` chỉ enable trong dev với env flag.
- Thêm test import boundary: production không import `backend.*`.

---

## 4. P0 Patch Plan — bắt buộc trước live money

### P0.1 — Live Order Atomic Lifecycle Patch

**Mục tiêu:** không có broker submit nếu ledger không ghi được.

**Files chính**
- `services/execution-service/execution_service/execution_engine.py`
- `apps/api/app/services/order_ledger_service.py`
- `apps/api/app/models/__init__.py`
- `apps/api/alembic/versions/*`

**Yêu cầu**
- `mark_submitting_hook` fail → return blocked, không route broker.
- `enqueue_unknown_hook` fail → create critical incident + daily lock.
- Add `OrderLifecycleUnitOfWork` transaction.
- Add test crash-after-submitting.

---

### P0.2 — Broker Live Contract Patch

**Mục tiêu:** live provider không được fallback/stub.

**Files chính**
- `services/execution-service/execution_service/providers/base.py`
- `services/execution-service/execution_service/providers/ctrader_live.py`
- `services/execution-service/execution_service/providers/ctrader.py`
- `services/execution-service/execution_service/providers/mt5.py`
- `services/execution-service/execution_service/providers/bybit.py`

**Yêu cầu**
- Tách `DemoProvider` và `LiveProvider`.
- Startup capability proof bắt buộc.
- Required methods cho live: account, quote, spec, margin, submit, query order, query execution, open positions, cancel pending, close all.
- CI chặn `stub/paper/degraded` trong live.

---

### P0.3 — Broker-Native Risk Context Patch

**Mục tiêu:** live sizing không dùng fallback.

**Files chính**
- `services/trading-core/trading_core/risk/risk_context_builder.py`
- `services/trading-core/trading_core/risk/broker_native_risk_context.py`
- `services/trading-core/trading_core/risk/position_sizing.py`
- `services/trading-core/trading_core/runtime/bot_runtime.py`

**Yêu cầu**
- Live fail nếu thiếu spec/quote/margin.
- Hash risk context vào frozen gate.
- Test XAUUSD/BTCUSD/JPY/crypto precision.

---

### P0.4 — Frozen Gate Context V1 Patch

**Mục tiêu:** order request không thể khác context đã duyệt.

**Files chính**
- `services/trading-core/trading_core/runtime/pre_execution_gate.py`
- `services/trading-core/trading_core/runtime/frozen_context_contract.py`
- `services/execution-service/execution_service/execution_engine.py`

**Yêu cầu**
- Typed `GateContextV1`.
- Bind symbol/side/volume/broker/account/idempotency/policy/quote.
- Hash canonical JSON.
- Fail closed nếu mismatch.

---

### P0.5 — Unknown Order Real Broker Reconciliation Patch

**Mục tiêu:** timeout/unknown order được tìm thật trên broker, không chỉ retry queue.

**Files chính**
- `apps/api/app/workers/reconciliation_daemon.py`
- `services/execution-service/execution_service/unknown_order_reconciler.py`
- `services/execution-service/execution_service/reconciliation_worker.py`
- `apps/api/app/services/reconciliation_queue_service.py`

**Yêu cầu**
- Query broker theo client id / broker order id / deal id.
- Upsert daily lock nếu escalation.
- Incident dedupe key.
- Manual operator resolve.

---

### P0.6 — Daily TP/Loss Runtime Lock Orchestrator Patch

**Mục tiêu:** đến TP/loss ngày thì dừng trade thật, không mở lệnh mới.

**Files chính**
- `apps/api/app/services/daily_lock_runtime_controller.py`
- `apps/api/app/services/daily_profit_lock_engine.py`
- `apps/api/app/services/daily_trading_state.py`
- `apps/web/components/live/DailyLockPanel.tsx`

**Yêu cầu**
- Lock exactly-once bằng row/advisory lock.
- Stop runtime + cancel pending + close positions optional.
- Block restart cùng ngày.
- Operator override có audit + approval.

---

### P0.7 — Production Legacy Isolation Patch

**Mục tiêu:** production không thể chạy legacy stack.

**Files chính**
- `apps/api/app/main.py`
- `apps/api/app/routers/legacy.py`
- `backend/*`
- `.github/scripts/verify_production_no_legacy_stack.sh`
- Dockerfiles

**Yêu cầu**
- Không include legacy router ở prod.
- Production image không copy `backend/`, `frontend/` legacy.
- CI fail nếu `backend.` được import từ `apps/api`/`services`.

---

## 5. P1 Patch Plan — sau khi P0 pass

1. **Operator Live Dashboard Hardening**
   - Emergency stop.
   - Close all positions.
   - Manual reconciliation.
   - Broker quote monitor.

2. **Event Bus / Audit Stream**
   - Publish lifecycle events to Redis/Kafka/SSE.
   - UI realtime timeline.

3. **Backtest/Paper/Live Parity**
   - Same signal → same policy/gate path.
   - Paper cannot skip order ledger.

4. **Strategy Governance**
   - Engine capability registry.
   - AI brain cannot mutate live policy without approval.

5. **Portfolio Risk Engine**
   - Cross-symbol exposure.
   - Correlation groups.
   - Max daily trades, session filters, news blackout.

---

## 6. P2 Patch Plan — nâng cấp hệ thành trading platform thật

1. Multi-account broker router.
2. Copy trading / allocation engine.
3. Market regime classifier.
4. Walk-forward validation pipeline.
5. Slippage/latency analytics.
6. Broker incident RCA + postmortem memory.
7. Canary live mode với lot nhỏ bắt buộc trước full mode.
8. Compliance export: order audit, policy audit, incident audit.

---

## 7. Checklist trước khi bật live mode

Không bật live nếu chưa pass toàn bộ:

- [ ] DB migration single head pass.
- [ ] Production image không chứa legacy backend.
- [ ] Broker provider capability proof pass.
- [ ] Account id/equity sync pass.
- [ ] Symbol spec + quote + margin estimate pass.
- [ ] Daily state fresh < 60s.
- [ ] No active daily lock.
- [ ] No unresolved unknown orders.
- [ ] No critical open incident.
- [ ] Reconciliation daemon heartbeat ok.
- [ ] Policy approval hash match runtime hash.
- [ ] GateContextV1 hash binds request exactly.
- [ ] Order lifecycle atomic unit test pass.
- [ ] Broker timeout → unknown queue → reconciliation test pass.
- [ ] Daily TP hit → lock + stop + block restart test pass.
- [ ] Manual emergency stop tested on demo.

---

## 8. Thứ tự triển khai đề xuất

1. **P0.1 Live Order Atomic Lifecycle**
2. **P0.4 Frozen Gate Context V1**
3. **P0.2 Broker Live Contract**
4. **P0.3 Broker-Native Risk Context**
5. **P0.5 Unknown Order Real Reconciliation**
6. **P0.6 Daily TP/Loss Runtime Lock**
7. **P0.7 Production Legacy Isolation**
8. Sau đó mới làm UI/operator và analytics P1.

---

## 9. Kết luận cuối

Bản `forex-main-6(1).zip` đã có khung của một hệ live trading nghiêm túc: runtime, risk gate, order ledger, broker execution, reconciliation, daily lock và operator UI. Nhưng để chuyển từ “có module” sang “chạy tiền thật”, cần đóng các vòng P0 sau:

- **Không submit nếu ledger không atomic.**
- **Không live nếu provider còn fallback/stub.**
- **Không risk nếu thiếu broker-native context.**
- **Không order nếu frozen context mismatch.**
- **Không bỏ mặc unknown order.**
- **Không trade tiếp sau daily TP/loss.**
- **Không cho legacy xuất hiện trong production.**

Sau khi hoàn thiện các patch này, hệ thống có thể tiến tới giai đoạn **demo live soak test 2–4 tuần**, sau đó mới cân nhắc live tiền nhỏ với canary lot và kill-switch 24/7.
