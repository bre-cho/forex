# FOREX MAIN 13 — Deep Code Audit & Completion Plan

**Scope:** `forex-main(13).zip`  
**Goal:** phân tích kỹ từng file/module/engine và đề xuất hoàn thiện toàn bộ code thành hệ thống live trading thật.

> Lưu ý vận hành: không có bot nào đảm bảo lợi nhuận 500%. Mục tiêu của patch plan này là biến repo thành hệ thống có thể kiểm soát rủi ro, fail-closed, có audit trail, có broker reconciliation và có điều kiện an toàn để demo/live có giám sát.

---

## 1. Executive Verdict

Repo bản 13 đã tiến bộ mạnh so với các bản trước. Nhiều khối P0 đã có mặt:

- Monorepo rõ ràng: `apps/api`, `apps/web`, `apps/admin`, `services/trading-core`, `services/execution-service`, `services/*`.
- Có Alembic migration đến `0012_order_idempotency_projection.py`.
- Có `LiveStartPreflight`, `LiveReadinessGuard`, `DailyProfitLockEngine`, `DailyLockRuntimeController`.
- Có `ExecutionEngine`, `PreExecutionGate`, `RiskContextBuilder`, `OrderStateMachine`, `UnknownOrderReconciler`, `ReconciliationWorker`.
- Có CI guard scripts: live no stub, broker gate wiring, frontend API contract, production no legacy stack.
- Có frontend/admin pages cho runtime, broker health, operations dashboard.

**Kết luận kỹ thuật:** bản 13 đã gần production hơn, nhưng **chưa nên chạy tiền thật không giám sát**. Lý do chính không còn là thiếu module, mà là **module đã có nhưng chưa khép kín end-to-end như một live trading control plane thật**.

### Blocking issues còn lại

1. **Execution lifecycle chưa hoàn toàn DB-source-of-truth.** `ExecutionEngine` trả `OrderResult`, có state machine và reconciler, nhưng cần một orchestration layer ghi đủ: intent → gate → reservation → broker attempt → receipt → projection → trade/position.
2. **Unknown order reconciliation vẫn là service logic, chưa chứng minh có worker scheduler + DB lock + retry policy + operator escalation thật trong API runtime.**
3. **Risk context đã tính tốt hơn nhưng vẫn cần broker-native instrument spec, margin estimate, tick value, contract size, min lot, lot step cho từng symbol trước live.**
4. **Legacy backend còn tồn tại.** Đã có CI guard, nhưng production compose/nginx/API cần khóa tuyệt đối không expose legacy `/api/*` khi `ENV=production`.
5. **Provider cTrader/MT5/Bybit chưa đủ hardening cho live parity.** Base provider yêu cầu method live, nhưng cần contract tests bắt buộc cho từng provider.
6. **Daily TP/Daily Loss đã có engine nhưng cần biến thành operator-visible policy lifecycle: approve → activate → runtime lock → broker close/reduce-only → audit → unlock next session.**
7. **Frontend operator chưa đủ màn hình quyết định sống/chết:** chưa thấy full order ledger, broker attempt, unknown queue, daily lock status, margin/exposure heatmap, manual intervention.

---

## 2. Repository Inventory

### 2.1 Root / Infra

| Path | Vai trò | Đánh giá | Cần hoàn thiện |
|---|---|---:|---|
| `README.md` | Mô tả monorepo, quick start | Tốt | Thêm live readiness runbook, broker credential guide, rollback procedure |
| `.env.example` | Env mẫu | Cần audit | Tách rõ `PAPER`, `DEMO`, `LIVE`; bắt buộc `LIVE_TRADING_ENABLED=false` default |
| `docker-compose.yml` | Compose tổng | Khá | Production phải không mount legacy services |
| `infra/docker/docker-compose.dev.yml` | Dev infra | Khá | Healthcheck Postgres/Redis/API/worker |
| `infra/docker/docker-compose.prod.yml` | Prod infra | P0 | Chặn legacy backend/frontend; thêm worker reconciliation; thêm metrics |
| `infra/nginx/*` | Reverse proxy | P0 | Không route legacy `/api/*` ở production nếu live enabled |
| `infra/monitoring/*` | Prometheus/Grafana/Loki | P1 | Add trading metrics dashboards |
| `.github/workflows/*` | CI | Tốt | Thêm full integration test với Postgres + Redis + provider fake live |

---

## 3. Apps/API Audit

### 3.1 `apps/api/app/main.py`

**Vai trò:** FastAPI entrypoint, route registration, middleware.

**Đánh giá:** đúng hướng vì API mới là control plane chính.

**Cần hoàn thiện:**

- P0: production mode phải fail nếu legacy router còn mounted khi `LIVE_TRADING_ENABLED=true`.
- P0: startup check phải verify DB migrations head, Redis, runtime registry, policy service, broker SDK availability.
- P1: expose `/health/live`, `/health/ready`, `/metrics` chuẩn Prometheus.

### 3.2 `apps/api/app/models/__init__.py`

**Vai trò:** SQLAlchemy model layer. Đã có các bảng core: users, workspaces, broker_connections, strategies, bot_instances, signals, orders, trades và các bảng live ledger/migration.

**Điểm tốt:**

- `orders` có `idempotency_key` và unique constraint theo bot.
- Có migration cho safety ledger, broker attempts, state transitions, execution receipts, daily profit lock, order projection.

**Lỗ hổng còn lại:**

- `orders.status` vẫn là string tự do. Cần enum hoặc constraint đồng bộ với `OrderStateMachine`.
- Cần bảng `positions` riêng làm projection từ broker, không chỉ dựa vào `trades`.
- Cần bảng `account_snapshots` làm source-of-truth cho equity/balance/free_margin/margin_level theo thời gian.
- Cần `broker_order_id` unique theo broker/account nếu provider có id thật.

**Patch đề xuất:**

```text
apps/api/alembic/versions/0013_live_position_projection.py
apps/api/alembic/versions/0014_order_status_constraints.py
apps/api/alembic/versions/0015_broker_account_snapshots_hardening.py
apps/api/app/services/order_ledger_service.py
apps/api/app/services/position_projection_service.py
```

### 3.3 `apps/api/app/services/live_start_preflight.py`

**Vai trò:** chặn bot live nếu broker/policy/daily state/incident không đạt.

**Điểm tốt:**

- Fail-closed khi provider không live-ready.
- Bắt policy approved.
- Sync equity từ broker, không fallback stale state.
- Block daily lock và critical incident.

**Cần hoàn thiện P0:**

- Check broker account mode thật: `demo` không được giả `live`.
- Check symbol tradable + market session open + leverage/margin rules.
- Check no unknown orders unresolved trước start.
- Check no orphan open positions outside ledger.
- Check active policy version hash khớp runtime snapshot.
- Check `LIVE_TRADING_ENABLED=true` và workspace entitlement.

**Patch:** `LiveStartPreflightV2` gồm checklist bắt buộc:

```text
broker_health
broker_account_mode_live
broker_symbol_tradable
broker_equity_synced
active_policy_approved
active_policy_hash_bound_to_runtime
daily_state_fresh
no_daily_lock
no_critical_incident
no_unknown_orders
no_orphan_positions
reconciliation_worker_running
operator_ack_required_for_first_live_start
```

### 3.4 `apps/api/app/services/daily_profit_lock_engine.py`

**Vai trò:** kiểm tra daily TP/daily loss, lock khi đạt ngưỡng.

**Đánh giá:** đúng hướng.

**Cần hoàn thiện:**

- Daily TP phải tính theo **realized PnL + floating PnL policy** rõ ràng.
- Dùng timezone session theo broker/account, không chỉ UTC.
- Reset next trading day phải có job rõ ràng và audit.
- Lock reason phải enum: `DAILY_TP_HIT`, `DAILY_LOSS_HIT`, `MANUAL_LOCK`, `POLICY_BREACH`, `BROKER_RISK`.

### 3.5 `apps/api/app/services/daily_lock_runtime_controller.py`

**Vai trò:** biến lock thành hành động runtime: stop new orders, close all, reduce-only.

**Điểm tốt:** Có thiết kế đúng.

**Cần hoàn thiện P0:**

- `close_all_and_stop` phải tạo close attempts vào DB, không chỉ gọi provider.
- Nếu close fail/timeout → incident critical + retry queue.
- `reduce_risk_only` cần enforce trong `PreExecutionGate`, không chỉ runtime flag.
- Cần endpoint operator xem lock action result.

### 3.6 `apps/api/app/services/bot_service.py`

**Vai trò:** tạo runtime, provider, hook DB events.

**Điểm tốt:**

- Live mode không cho fallback stub nếu import runtime lỗi.
- Có `_assert_provider_usable`.
- Có runtime readiness guard.
- Có hook idempotency/daily/reconciliation.

**P0 còn lại:**

- `_register_stub` vẫn tồn tại cho non-live. OK cho paper, nhưng production CI phải đảm bảo không thể vào live.
- Cần tách `RuntimeBootstrapService` riêng, vì `bot_service.py` đang quá lớn.
- Cần atomic transaction khi start bot live: set status `starting` → preflight → runtime create → broker connect → status `running`; fail thì rollback/status `failed`.
- Runtime hooks cần contract tests: `on_order`, `reserve_idempotency`, `verify_idempotency_reservation`, `on_reconciliation_result`.

---

## 4. Trading Core Audit

### 4.1 `services/trading-core/trading_core/runtime/bot_runtime.py`

**Vai trò:** vòng đời bot, signal loop, pre-execution, provider interaction, snapshot.

**Đánh giá:** đây là trái tim runtime. Có nhiều guard live, nhưng file lớn nên dễ drift.

**Cần hoàn thiện:**

- Tách thành các sub-engine:
  - `SignalLoopEngine`
  - `DecisionCycleEngine`
  - `PreTradeRiskOrchestrator`
  - `ExecutionCommandBuilder`
  - `RuntimeSnapshotEmitter`
  - `RuntimeKillSwitchController`
- Mỗi cycle phải có `brain_cycle_id` bất biến.
- Mọi trade intent phải sinh `idempotency_key` trước khi gọi execution.
- Nếu order result `UNKNOWN` → bắt buộc vào reconciliation queue.

### 4.2 `runtime/pre_execution_gate.py`

**Vai trò:** hard gate trước khi đặt lệnh.

**Điểm tốt:**

- Block live khi provider stub/degraded/unavailable.
- Check broker connection, market data, data age, daily TP/loss, margin, exposure, slippage, spread, open positions, duplicate idempotency, confidence, RR.

**Cần hoàn thiện P0:**

- Thêm `reduce_only_mode`: chỉ cho lệnh giảm/đóng vị thế.
- Thêm `symbol_tradable=false` block.
- Thêm `market_session_closed` block.
- Thêm `unknown_order_queue_not_empty` block cho mở lệnh mới.
- Thêm `manual_override_required` nếu first live start hoặc policy mới.

### 4.3 `risk/risk_context_builder.py`

**Vai trò:** tính margin usage, free margin, exposure, pip value, max loss.

**Điểm tốt:**

- Live mode bắt buộc có instrument spec, không dùng fallback.
- Dùng contract size/margin rate từ spec.
- Có account/symbol/correlated exposure.

**Cần hoàn thiện P0:**

- Không tự tính margin bằng công thức đơn giản nếu broker có `estimate_margin`; live phải dùng broker estimate.
- Correlation không chỉ `USD in symbol`. Cần correlation bucket theo base/quote, metals, crypto, indices.
- Pip value cần quy đổi account currency nếu account không phải USD.
- Cần validate min lot/max lot/step trước order.

### 4.4 `risk/position_sizing.py`

**Vai trò:** tính lot theo equity/risk/SL.

**Điểm tốt:** cơ bản đúng.

**Cần hoàn thiện:**

- Không được force lên `min_lot` nếu raw lot < min và risk thực tế vượt policy. Phải return `BLOCK: below_min_lot_or_risk_exceeds`.
- Cần expose `actual_risk_pct_after_rounding`.
- Cần kiểm tra stop loss hợp lệ theo broker minimum stop distance.

### 4.5 `data/market_data_quality.py`

**Vai trò:** kiểm tra OHLC, gap, duplicate timestamp, spread.

**Điểm tốt:** đã có basic quality gate.

**Cần hoàn thiện:**

- Thêm stale tick check theo realtime feed.
- Thêm outlier candle/wick spike detection.
- Spread pip size phải dùng instrument spec, không hardcode `*10000`.
- Crypto/JPY/metals pip calculation riêng.

---

## 5. Execution Service Audit

### 5.1 `execution_service/execution_engine.py`

**Vai trò:** route order qua provider, gate, timeout.

**Điểm tốt:**

- Live yêu cầu `ExecutionCommand`, `brain_cycle_id`, `idempotency_key`, `pre_execution_context`.
- Verify idempotency reservation trước broker submit.
- Timeout trả `submit_status=UNKNOWN`, `fill_status=UNKNOWN`.

**P0 cần hoàn thiện:**

- Engine không nên chỉ return `OrderResult`; phải phát event hoặc gọi hook để persist attempt ngay trước và sau broker submit.
- Trạng thái `UNKNOWN` phải tự động enqueue reconciliation.
- Cần phân biệt:
  - broker submit timeout trước khi broker nhận lệnh
  - broker ACK timeout sau khi broker nhận
  - fill timeout
- Cần attach `client_order_id` vào broker request bắt buộc.

**Patch:**

```text
execution_service/order_execution_orchestrator.py
execution_service/order_event_sink.py
execution_service/reconciliation_queue.py
```

### 5.2 `order_state_machine.py`

**Vai trò:** validate transition.

**Điểm tốt:** Có lifecycle rõ: `INTENT_CREATED → GATE_ALLOWED → RESERVED → SUBMITTED → ACKED/FILLED/PARTIAL/UNKNOWN...`

**Cần hoàn thiện:**

- Thêm terminal states: `CANCELED`, `EXPIRED`, `CLOSE_REQUESTED`, `CLOSE_SUBMITTED`, `CLOSE_FILLED`.
- Không cho `SUBMITTED → FILLED` nếu thiếu receipt/fill execution.
- State machine cần reason code + actor: runtime, broker, reconciler, operator.

### 5.3 `unknown_order_reconciler.py`

**Vai trò:** query broker theo client id/idempotency để xử lý UNKNOWN.

**Điểm tốt:** đúng logic cần có.

**Cần hoàn thiện P0:**

- Worker phải đọc unknown orders từ DB với `FOR UPDATE SKIP LOCKED`.
- Retry policy phải lưu `attempt_count`, `next_retry_at`, `last_error`.
- Nếu provider không hỗ trợ query by client id → live phải degraded/block mở lệnh mới.
- Kết quả reconciliation phải project vào `orders`, `trades`, `positions`, `execution_receipts`.

### 5.4 Providers

| File | Đánh giá | Cần hoàn thiện |
|---|---|---|
| `providers/base.py` | Contract tốt | Live provider phải implement toàn bộ optional methods, không optional trong live |
| `providers/paper.py` | OK cho test | Không import được trong live path |
| `providers/ctrader.py` | Có hardening | Cần real API parity/integration test |
| `providers/ctrader_execution_adapter.py` | Quan trọng | Bắt buộc client_order_id, receipt parsing, partial fill |
| `providers/mt5.py` | Stub/dev warning | Không live-ready nếu SDK absent |
| `providers/bybit.py` | Stub/dev warning | Cần crypto-specific position/risk/contract rules |

---

## 6. AI Trading Brain Audit

### Files

- `ai_trading_brain/brain_contracts.py`
- `ai_trading_brain/brain_runtime.py`
- `ai_trading_brain/decision_engine.py`
- `ai_trading_brain/engine_registry.py`
- `ai_trading_brain/evolution_engine.py`
- `ai_trading_brain/governance.py`
- `ai_trading_brain/memory_engine.py`
- `ai_trading_brain/unified_trade_pipeline.py`

**Đánh giá:** Đây là layer decision/AI. Có kiểm tra broker stub trong unified pipeline.

**P0 cần hoàn thiện trước live:**

- AI không được trực tiếp quyết định volume cuối cùng. Volume phải qua `RiskContextBuilder + PositionSizing + PreExecutionGate`.
- Decision output phải là `TradeIntent`, không phải `OrderRequest` live trực tiếp.
- Cần `DecisionAuditRecord`: input hash, model version, strategy version, risk policy hash, output reason.
- Tắt hoặc sandbox `evolution_engine` trong live. Không cho self-evolution thay policy khi đang live.
- LLM orchestrator/stub chỉ nên dùng để phân tích, không được bypass deterministic gates.

---

## 7. Legacy Backend Audit

### `backend/`

Có các engine legacy tương tự trading-core:

- `risk_manager.py`
- `trade_manager.py`
- `decision_engine.py`
- `ctrader_provider.py`
- `auto_pilot.py`
- nhiều AI engines

**Vấn đề:** legacy có mock/stub và `/api/*` compatibility. Với live trading thật, legacy là rủi ro drift.

**Quyết định đề xuất:**

- Development: giữ legacy read-only để so sánh.
- Production: không mount legacy route.
- CI: nếu legacy thay đổi mà service mới không thay đổi → fail.
- Sau P0: archive legacy vào `legacy/` hoặc xóa khỏi deployment image.

---

## 8. Frontend/Admin Audit

### `apps/web`

Có API clients:

- `brokerApi.ts`
- `runtimeApi.ts`
- `riskPolicyApi.ts`
- `decisionLedgerApi.ts`
- `tradingBrainApi.ts`

**Cần hoàn thiện:**

- Trang Live Readiness Checklist.
- Trang Order Ledger: intent/gate/reservation/attempt/receipt/state/projection.
- Trang Unknown Orders Queue.
- Trang Daily Lock Control: TP/loss, lock reason, action result, unlock schedule.
- Trang Risk Heatmap: margin/exposure/correlation.
- Nút operator: pause new orders, reduce-only, close all, emergency stop.

### `apps/admin`

Có:

- broker health
- operations dashboard
- runtime
- users/workspaces

**Cần hoàn thiện:**

- Admin phải xem toàn hệ thống: bots live, incidents, unknown orders, unresolved reconciliation, provider degraded.
- Thêm alert severity: info/warning/critical.
- Thêm audit export CSV/JSON cho compliance.

---

## 9. CI/Test Audit

Repo đã có nhiều test và verify scripts tốt:

- `verify_live_no_stub.sh`
- `verify_no_live_stub_provider.py`
- `verify_live_safety_closure.sh`
- `verify_broker_gate_wiring.py`
- `verify_market_data_quality_scenarios.sh`
- `verify_runtime_snapshot_payload.py`
- tests cho execution, state machine, preflight, daily lock.

**Cần thêm P0:**

```text
.github/scripts/verify_live_legacy_not_mounted.py
.github/scripts/verify_order_ledger_full_lifecycle.py
.github/scripts/verify_unknown_order_worker_wired.py
.github/scripts/verify_provider_live_contracts.py
.github/scripts/verify_daily_lock_close_all_audit.py
.github/scripts/verify_risk_context_broker_margin.py
```

**Integration tests bắt buộc:**

1. Paper E2E: signal → intent → order → filled → trade → daily state.
2. Fake-live E2E: broker ACK → partial fill → full fill → position projection.
3. Timeout E2E: submit timeout → UNKNOWN → reconciler finds filled → ledger updated.
4. Daily TP E2E: profit hits target → lock → stop new orders → close/reduce-only.
5. Provider degraded E2E: broker health bad → live preflight blocked.
6. Legacy production E2E: production env → legacy route unavailable.

---

## 10. Production Completion Roadmap

## P0 — Live Safety Closure

### P0.1 Order Ledger Source-of-Truth

**Add files:**

```text
apps/api/app/services/order_ledger_service.py
apps/api/app/services/execution_receipt_service.py
apps/api/app/services/position_projection_service.py
apps/api/app/services/reconciliation_queue_service.py
apps/api/alembic/versions/0013_live_position_projection.py
apps/api/alembic/versions/0014_reconciliation_queue.py
```

**Rules:**

- Không gọi broker nếu chưa có order intent persisted.
- Không coi order success nếu thiếu broker receipt.
- `UNKNOWN` không được im lặng, phải vào queue.
- `orders` chỉ là projection; raw truth nằm ở attempts/receipts/transitions.

### P0.2 Live Provider Contract Hardening

**Add tests:**

```text
services/execution-service/tests/test_live_provider_contract_ctrader.py
services/execution-service/tests/test_live_provider_contract_mt5.py
services/execution-service/tests/test_live_provider_contract_bybit.py
```

**Required live methods:**

```text
connect
health_check
get_account_info
get_instrument_spec
estimate_margin
place_order with client_order_id
get_order_by_client_id
get_executions_by_client_id
get_open_positions
close_all_positions
```

### P0.3 Risk Context Broker-Native

**Patch:**

```text
trading_core/risk/risk_context_builder.py
trading_core/risk/position_sizing.py
trading_core/runtime/pre_execution_gate.py
```

**Rules:**

- Live: margin estimate phải từ broker.
- Live: instrument spec phải từ broker.
- Live: pip/tick value phải đúng account currency.
- Rounding lot không được làm risk vượt policy.

### P0.4 Daily TP/Loss Runtime Lock Full Loop

**Patch:**

```text
apps/api/app/services/daily_profit_lock_engine.py
apps/api/app/services/daily_lock_runtime_controller.py
apps/api/app/routers/risk_policy.py
apps/web/app/(app)/daily-lock/page.tsx
apps/admin/app/operations-dashboard/page.tsx
```

**Rules:**

- TP hit → lock new orders.
- Loss hit → lock + optional close all.
- Close all phải có attempts/receipts.
- Unlock next day phải audited.

### P0.5 Production No-Legacy Runtime

**Patch:**

```text
apps/api/app/main.py
apps/api/app/routers/legacy.py
infra/nginx/conf.d/default.conf
infra/docker/docker-compose.prod.yml
.github/scripts/verify_production_no_legacy_stack.sh
```

**Rule:**

- `ENV=production` + `LIVE_TRADING_ENABLED=true` → legacy route not mounted.

---

## P1 — Operator Control Plane

### P1.1 Live Operations Dashboard

Add pages:

```text
apps/web/app/(app)/live-readiness/page.tsx
apps/web/app/(app)/orders/page.tsx
apps/web/app/(app)/reconciliation/page.tsx
apps/web/app/(app)/risk/page.tsx
apps/admin/app/incidents/page.tsx
```

### P1.2 Realtime Event Stream

Use existing `events/` and `ws.py` to broadcast:

```text
ORDER_INTENT_CREATED
GATE_BLOCKED
ORDER_SUBMITTED
ORDER_ACKED
ORDER_UNKNOWN
RECONCILIATION_RESOLVED
DAILY_LOCK_TRIGGERED
BROKER_DEGRADED
KILL_SWITCH_ENABLED
```

### P1.3 Monitoring Metrics

Prometheus metrics:

```text
trading_orders_total{status}
trading_order_unknown_total
trading_reconciliation_latency_seconds
trading_daily_profit_amount
trading_daily_lock_active
broker_health_status
broker_submit_latency_ms
pre_execution_gate_blocks_total{reason}
```

---

## P2 — Strategy/AI Reliability

### P2.1 Deterministic Strategy Contract

AI brain only emits:

```text
TradeIntent(symbol, direction, confidence, entry_zone, stop_loss, take_profit, rationale_hash)
```

Runtime computes:

```text
volume, margin, exposure, idempotency_key, execution command
```

### P2.2 Backtest/Paper/Demo Parity

Same pipeline for all modes:

```text
signal → intent → gate → reservation → execution → receipt → projection → analytics
```

Mode only changes provider, not pipeline.

### P2.3 Strategy Version Freeze

Live bot must bind:

```text
strategy_version_hash
risk_policy_hash
model_version
provider_contract_version
```

No mutation while running live.

---

## 11. Final Architecture Target

```text
Market Data Provider
      ↓
MarketDataQualityEngine
      ↓
Signal/AI Brain → TradeIntent only
      ↓
RiskContextBuilder + PositionSizing
      ↓
PreExecutionGate
      ↓
OrderLedgerService: INTENT_CREATED
      ↓
IdempotencyReservation
      ↓
ExecutionEngine → BrokerProvider
      ↓
BrokerExecutionReceipt
      ↓
OrderStateMachine
      ↓
PositionProjection + AccountSnapshot
      ↓
DailyProfitLockEngine
      ↓
Runtime Controller / Operator Dashboard / Alerts
      ↓
UnknownOrderReconciler if timeout/unknown
```

---

## 12. Go/No-Go Checklist for Real Money

Không chạy live nếu bất kỳ mục nào fail:

- [ ] Production không expose legacy backend.
- [ ] Live provider implement đủ contract.
- [ ] Broker account mode xác thực đúng live/demo.
- [ ] Instrument spec lấy từ broker.
- [ ] Margin estimate lấy từ broker.
- [ ] Order ledger lưu đủ full lifecycle.
- [ ] Unknown order reconciler chạy như worker.
- [ ] Daily TP/loss lock test pass.
- [ ] Close all/reduce-only test pass.
- [ ] Operator có emergency stop.
- [ ] Prometheus/Grafana có alert critical.
- [ ] Migration single-head pass.
- [ ] Paper/demo E2E pass ít nhất 7 ngày forward test.
- [ ] Live chạy micro-lot/capital nhỏ với manual supervision trước khi scale.

---

## 13. Recommended Next Patch Order

1. **FINAL ORDER LEDGER SOURCE-OF-TRUTH PATCH**
2. **UNKNOWN ORDER WORKER DB-WIRED PATCH**
3. **BROKER LIVE CONTRACT PARITY PATCH**
4. **RISK CONTEXT BROKER-NATIVE MARGIN PATCH**
5. **DAILY TP/LOSS FULL RUNTIME LOCK PATCH**
6. **PRODUCTION NO-LEGACY HARD LOCK PATCH**
7. **OPERATOR LIVE DASHBOARD PATCH**
8. **PROMETHEUS TRADING METRICS PATCH**
9. **AI TRADE INTENT ONLY PATCH**
10. **7-DAY DEMO FORWARD TEST HARNESS**

---

## 14. Highest-Impact Patch to Build Next

**Build next:** `FINAL ORDER LEDGER SOURCE-OF-TRUTH PATCH`.

Vì hiện tại repo đã có nhiều guard, nhưng nếu order lifecycle chưa là source-of-truth tuyệt đối thì live trading vẫn có rủi ro lớn nhất: broker đã nhận lệnh nhưng hệ thống không biết, timeout thành UNKNOWN nhưng không xử lý, hoặc UI báo sai trạng thái.

Patch này phải làm 5 việc:

1. Persist order intent trước broker submit.
2. Persist idempotency reservation.
3. Persist broker attempt trước khi gọi provider.
4. Persist receipt/result sau provider.
5. Nếu UNKNOWN → enqueue reconciliation và block new orders nếu policy yêu cầu.

Sau patch này, hệ thống mới có nền tảng an toàn để hoàn thiện broker/risk/dashboard.
