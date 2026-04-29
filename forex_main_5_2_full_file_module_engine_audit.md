# FOREX-MAIN-5(2) — Deep File/Module/Engine Completion Audit

> Vai trò audit: Technical Lead live trading systems.  
> Mục tiêu: nghiên cứu code theo từng cụm file/module/engine và đề xuất hoàn thiện toàn bộ để tiến gần chuẩn **live trading thật**.  
> Repo phân tích: `/mnt/data/forex-main-5(2).zip` → giải nén thành `forex-main`.

---

## 1. Kết luận điều hành

Bản `forex-main-5(2)` đã tiến bộ rõ so với các bản trước: có monorepo, có `trading-core`, `execution-service`, `apps/api`, `apps/web`, ledger, daily lock, pre-execution gate, live preflight, unknown-order reconciliation, cTrader adapter split, CI verify scripts. Tuy nhiên **chưa nên chạy tiền thật không giám sát** vì hệ vẫn còn các rủi ro P0 ở lớp broker contract, source-of-truth ledger, live-only wiring, daily lock orchestration, reconciliation escalation và legacy isolation.

Đánh giá readiness:

| Hạng mục | Điểm | Trạng thái |
|---|---:|---|
| Kiến trúc monorepo | 8/10 | Tốt, đã tách service rõ |
| Trading brain / decision layer | 6.5/10 | Có guard, nhưng còn stub/legacy và chưa đủ audit explainability |
| Risk engine | 7/10 | Có broker-native risk context, nhưng cần chuẩn hóa instrument/margin/slippage/exposure |
| Execution engine | 7/10 | Có command + receipt + idempotency, nhưng cần harden broker adapter thật |
| Order ledger | 7/10 | Đã có lifecycle, projection, transitions; cần atomic outbox và idempotent event processor |
| Daily TP/loss lock | 6.5/10 | Có state + controller; cần runtime orchestrator exactly-once |
| Reconciliation | 7/10 | Có worker + daemon; cần unify queue/worker/daemon và broker-native lookup |
| API/operator | 6.5/10 | Có dashboard endpoints; cần action approval + live runbook UI |
| Frontend | 6/10 | Có panels live, nhưng chưa đủ operator command center |
| CI/safety | 7.5/10 | Nhiều verify scripts tốt; cần chạy bắt buộc trong release gate |
| Legacy isolation | 5.5/10 | Có README deprecated và guard, nhưng `backend/` vẫn chứa engine/fallback mock nguy hiểm |

**Verdict:** Repo đã ở mức “near-live architecture”, nhưng còn thiếu các lớp “fail-closed, broker-native, source-of-truth, exactly-once, operator-approved” để đạt chuẩn live trading thật.

---

## 2. Inventory tổng quan repo

Repo có 359 file, các cụm chính:

| Cụm | Số file | Vai trò |
|---|---:|---|
| `apps/api` | 72+ | FastAPI backend mới, DB models, routers, safety ledger, daily state, live preflight |
| `apps/web` | 50+ | Next.js web dashboard, live control center, orders, runtime panels |
| `apps/admin` | 7+ | Admin UI: broker health, operations dashboard, users/workspaces |
| `services/trading-core` | 40+ | Engine phân tích, runtime bot, risk, pre-execution gate, market data quality |
| `services/execution-service` | 20+ | Broker providers, execution engine, order state machine, reconciliation |
| `ai_trading_brain` | 9 | Brain runtime, contracts, governance, memory/evolution |
| `services/signal-service` | 5 | Signal builder/feed/scoring/broadcast |
| `services/analytics-service` | 6 | Drawdown, equity, expectancy, profit factor, Sharpe |
| `services/billing-service` | 4 | Entitlements, plan rules, Stripe client |
| `packages` | 6 | Shared schemas/config/ui/broker SDK placeholder |
| `.github` | 19 | CI workflows + safety verify scripts |
| `backend`, `frontend` | 37 | Legacy stack, có README deprecated nhưng vẫn còn code thật |

---

## 3. Kiến trúc hiện tại

Luồng chính hiện tại có thể đọc như sau:

```text
Market Data / Broker Provider
        ↓
BotRuntime.tick()
        ↓
WaveDetector / SignalCoordinator / BrainRuntime
        ↓
Risk sizing + RiskContextBuilder
        ↓
PreExecutionGate + frozen gate context hash
        ↓
DB idempotency reservation
        ↓
ExecutionCommand → ExecutionEngine
        ↓
BrokerProvider.place_order()
        ↓
OrderResult / receipt contract
        ↓
SafetyLedger / OrderLedger / Projection
        ↓
ReconciliationWorker / UnknownOrderReconciler / ReconciliationDaemon
        ↓
API + Operator UI
```

Điểm đúng: hệ đã cố gắng đưa order qua gate, reserve idempotency, validate frozen context, enforce live receipt, rồi mới ghi trade. Đây là hướng đúng cho live trading.

Điểm chưa đủ: vẫn có nhiều đường phụ, legacy code, fallback paper/demo, adapter live chưa chứng minh có broker API thật, và event persistence chưa atomic end-to-end.

---

## 4. Phân tích từng module chính

### 4.1 `ai_trading_brain/`

| File | Vai trò hiện tại | Vấn đề | Đề xuất hoàn thiện |
|---|---|---|---|
| `brain_contracts.py` | Định nghĩa input/output brain | Default broker vẫn là `stub`; dễ lọt nếu caller không override | Live mode phải require broker thật, account_id, policy_version, cycle_id, risk snapshot |
| `brain_runtime.py` | Runtime chạy brain cycle | Có thể block/skip nhưng cần audit explainability mạnh hơn | Ghi `decision_trace`, feature snapshot, prompt/model/version nếu có LLM |
| `decision_engine.py` | Quyết định action | Cần chuẩn hóa action enum với execution gate | Enum duy nhất: ALLOW/SKIP/BLOCK/PAUSE/REDUCE_ONLY |
| `engine_registry.py` | Registry engine | Có ích nhưng chưa gắn health/live readiness | Thêm health contract per engine: required/optional/degraded |
| `governance.py` | Rule/governance | Chưa thấy gắn chặt vào policy approval DB | Bind với `PolicyService` và live approval version |
| `memory_engine.py` | Memory | Nếu dùng cho live, cần audit immutability | Tách memory research khỏi live decision path hoặc log immutable snapshot |
| `evolution_engine.py` | Evolution/tự tối ưu | Rủi ro cao trong live | Cấm auto-evolve live. Chỉ paper/backtest/canary, cần approval trước promote |
| `unified_trade_pipeline.py` | Pipeline thống nhất | Có check `live_stub_broker_forbidden` tốt | Bắt buộc mọi live decision đi qua pipeline này, không đi trực tiếp legacy |

**Patch P0:** tạo `BrainLiveDecisionContract` bắt buộc đủ: `cycle_id`, `model_version`, `policy_version`, `broker_name`, `account_id`, `symbol`, `side`, `risk_snapshot_hash`, `reason_codes`, `approved_action`. Nếu thiếu → BLOCK.

---

### 4.2 `services/trading-core/trading_core/runtime/`

| File | Vai trò | Nhận xét | Patch cần làm |
|---|---|---|---|
| `bot_runtime.py` | Core runtime loop của bot | Là file quan trọng nhất; có live path, brain, risk, gate, idempotency, execution, reconciliation | Cần chia nhỏ: `SignalCycleService`, `RiskPreparationService`, `ExecutionPreparationService`, `RuntimeEventEmitter` để giảm file quá lớn và tránh side-effect |
| `pre_execution_gate.py` | Gate cuối trước khi đặt lệnh | Tốt: block stub provider, kill switch, daily lock, stale data, spread, margin, exposure, SL | Cần fail-closed nếu live thiếu các field bắt buộc, hiện một số field default về 0 có thể che lỗi |
| `frozen_context_contract.py` | Bind request với frozen gate context | Tốt: check symbol/side/volume/idempotency/broker/policy/hash | Cần bind thêm `account_id`, `broker_account_id`, `strategy_version`, `risk_policy_hash`, `quote_timestamp`, `expected_slippage` |
| `runtime_factory.py` | Tạo runtime | Cần đảm bảo live không rơi về paper provider | Release gate phải test live provider branch thật |
| `runtime_registry.py` | Quản lý runtime | Cần expose `pause_new_orders`, `stop`, `set_risk_mode` chắc chắn vì DailyLockRuntimeController phụ thuộc | Thêm interface contract + tests |
| `runtime_state.py` | State runtime | Cần phân biệt `running`, `paused`, `new_orders_paused`, `reduce_only`, `locked` | Thêm explicit fields thay vì nhét metadata |

**P0 nguy hiểm nhất ở `bot_runtime.py`:** file này đang làm quá nhiều việc: fetch data, tạo signal, chạy brain, sizing, gate, reserve idempotency, gọi broker, xử lý receipt, emit event, mở trade. Khi live, một bug nhỏ có thể làm lệch state. Cần tách thành pipeline có checkpoint bất biến.

**Đề xuất pipeline chuẩn:**

```text
CycleStarted
→ MarketDataValidated
→ BrainDecisionCreated
→ RiskContextBuilt
→ GateEvaluated
→ IdempotencyReserved
→ ExecutionCommandFrozen
→ BrokerSubmitStarted
→ BrokerReceiptReceived / BrokerSubmitUnknown
→ LedgerProjected
→ ReconciliationScheduled / PositionVerified
```

Mỗi bước phải ghi event có `cycle_id`, `idempotency_key`, `hash`, `previous_hash`.

---

### 4.3 `services/trading-core/trading_core/risk/`

| File | Vai trò | Điểm tốt | Vấn đề cần sửa |
|---|---|---|---|
| `risk_context_builder.py` | Tính margin/exposure/max loss | Live mode yêu cầu instrument spec + broker margin estimate | Exposure vẫn là approximation theo notional, correlation USD còn đơn giản |
| `broker_native_risk_context.py` | Broker-native margin estimate | Đúng hướng: live không tự tính margin | Cần bắt provider trả currency, margin mode, leverage, conversion rate |
| `position_sizing.py` | Tính lot theo risk | Có min/max/step | Cần validate lot theo broker precision + reject nếu rounded risk vượt policy |
| `instrument_spec.py` | Spec symbol/fallback | Fallback chỉ nên dùng paper/backtest | Cần provider spec cache có TTL + source = broker |
| `pip_value.py` | Pip size/value | Có thể sai với JPY, metals, crypto, CFD | Live phải dùng broker-native pip value/conversion |
| `daily_profit_policy.py` | Resolve daily TP target | Có cấu trúc policy tốt | Cần thống nhất với DailyProfitLockEngine và PreExecutionGate |
| `exposure_guard.py` | Exposure guard | Cần kiểm tra cross-symbol exposure | Thêm correlation matrix + portfolio-level exposure |

**Patch P0:** tạo `BrokerNativeInstrumentSpec` gồm:

```python
symbol, broker_symbol_id, base_asset, quote_asset, account_currency,
contract_size, pip_size, tick_size, tick_value, lot_step,
min_lot, max_lot, margin_rate, leverage, commission_model,
swap_model, trading_session, market_status, source_timestamp
```

Live risk context chỉ được build nếu spec này đến từ broker và còn fresh.

---

### 4.4 `services/execution-service/execution_service/`

| File | Vai trò | Nhận xét | Patch cần làm |
|---|---|---|---|
| `execution_engine.py` | Orchestrates submit order | Có ExecutionCommand, verify reservation, hash gate, receipt contract | Cần atomic lifecycle: `SUBMITTING` trước broker call, then ACK/UNKNOWN/REJECTED |
| `order_router.py` | Router provider | Đơn giản | Cần enforce provider allowlist theo runtime mode |
| `order_state_machine.py` | State transitions | Có allowed transitions khá tốt | Thiếu một số trạng thái thực tế: `SUBMITTING`, `ACK_TIMEOUT`, `PARTIAL_OPEN`, `CLOSE_SUBMITTED`, `CLOSE_UNKNOWN` |
| `parity_contract.py` | Contract paper/live | Tốt để test payload | Cần đưa vào CI mandatory + runtime hard-block |
| `account_sync.py` | Sync account | Có silent `pass` trong exception path | Live không được nuốt lỗi account sync; nếu fail nhiều lần → pause/lock |
| `reconciliation_worker.py` | Reconcile DB vs broker | Có ghost position critical | Cần thật sự pause runtime/new orders qua registry; hiện incident là chưa đủ |
| `unknown_order_reconciler.py` | Resolve UNKNOWN by client id | Đúng hướng | Cần broker-native lookup thật, partial-fill handling và deadline escalation |

**Điểm P0:** `ExecutionEngine._enforce_live_receipt_contract()` yêu cầu `ACKED`, `FILLED/PARTIAL`, `broker_order_id/position_id`, `account_id`, `raw_response_hash`. Đây là tốt. Nhưng trước khi gọi broker, hệ cần ghi `SUBMITTING` vào ledger. Nếu process chết giữa broker call và receipt, DB phải biết có một order đang in-flight.

**Patch đề xuất:**

```text
reserve idempotency
→ create broker_order_attempt(status=SUBMITTING)
→ call broker with client_order_id
→ if success receipt valid: status=ACKED/FILLED
→ if timeout/network/invalid receipt: status=UNKNOWN + enqueue recon
→ if rejected: status=REJECTED
```

Không cho ghi `Trade(open)` nếu chưa có `OPEN_POSITION_VERIFIED` từ broker.

---

### 4.5 Broker providers

#### `providers/base.py`

Đây là contract broker chính. Đã có `OrderRequest`, `OrderResult`, `ExecutionReceipt`, `AccountInfo`, `PreExecutionContext`, `ExecutionCommand`, `BrokerProvider`.

**Thiếu P0:**

- `account_id` chưa nằm trong `AccountInfo`, nhưng live receipt/gate lại cần account_id.
- `OrderRequest` chưa có `time_in_force`, `slippage_tolerance`, `reduce_only`, `magic_number/strategy_tag`, `request_timestamp`.
- `OrderResult` chưa chuẩn hóa `requested_volume`, `filled_volume`, `remaining_volume`, `reject_code`, `broker_latency_ms`, `server_time` mandatory trong live.
- `ExecutionReceipt` chưa được dùng thống nhất thay vì `OrderResult`.

**Patch:** chuyển live path sang `ExecutionReceipt` chuẩn, `OrderResult` chỉ là projection/UI.

#### `providers/ctrader.py`, `ctrader_live.py`, `ctrader_execution_adapter.py`, `ctrader_market_data_adapter.py`

Điểm tốt:

- Có split execution/market data adapter.
- Live mode check account_id, execution adapter, market data.
- `get_quote()` live fail-closed nếu không có quote thật.
- `supports_client_order_id=True`.

Rủi ro còn lại:

- `CTraderEngineExecutionAdapter` chỉ wrap engine nếu engine có method, chưa chứng minh đó là cTrader Open API thật.
- `place_market_order()` chỉ truyền `comment/client_order_id` nếu underlying provider hỗ trợ signature. Nếu không hỗ trợ mà live vẫn `supports_client_order_id=True`, có thể gây idempotency giả.
- `get_order_by_client_id()` fallback search history bằng comment; live dùng history fallback có thể chậm/sai nếu broker API không đảm bảo.
- `get_candles()` market adapter là sync, trong runtime gọi qua async provider wrapper; cần thống nhất async.

**Patch P0:**

1. Tách `CTraderLiveOpenApiProvider` riêng, không wrap engine legacy.
2. Live provider phải implement trực tiếp:
   - authorize account
   - symbol resolution
   - quote stream
   - order submit with clientMsgId
   - order/deal lookup by clientMsgId
   - positions stream
   - server time
   - margin estimate
3. Nếu không có `clientMsgId` roundtrip proof → live startup fail.

#### `providers/mt5.py`, `bybit.py`, `paper.py`

- `paper.py` dùng cho test/dev là hợp lý.
- `mt5.py`, `bybit.py` có stub/paper mode warning. Live phải bị chặn nếu SDK/session không thật.
- Với Bybit crypto, cần khác Forex: qty precision, leverage, margin mode, reduce-only, funding, mark price, liquidation risk.

**Patch:** tạo broker capability matrix:

```json
{
  "provider": "ctrader",
  "mode": "live",
  "supports_client_order_id": true,
  "supports_order_lookup": true,
  "supports_execution_lookup": true,
  "supports_margin_estimate": true,
  "supports_live_quote": true,
  "supports_close_all_positions": true,
  "proof_checked_at": "..."
}
```

Live start chỉ allow nếu all required true.

---

### 4.6 `apps/api/app/models` + migrations

Repo có migration từ `0001` đến `0017`, bao gồm:

- initial schema
- trade lifecycle status
- live trading safety ledger
- order idempotency reservations
- broker order attempts
- order state transitions
- execution receipts
- policy approval control plane
- daily profit lock policy
- order state machine
- account snapshots/experiments
- idempotency projection
- reconciliation queue
- orders projection and transition idempotency
- broker attempt gate context hash
- execution receipt contract
- reconciliation queue lease

Đây là rất tốt. Tuy nhiên cần kiểm tra bằng CI rằng DB thực tế migrate clean từ 0 → latest và downgrade không bắt buộc nhưng upgrade phải pass.

**Patch P0 DB:**

- Unique constraint bắt buộc: `(bot_instance_id, idempotency_key)` trên attempts/reservations/orders/reconciliation queue.
- `broker_execution_receipts.raw_response_hash` not null trong live.
- `broker_order_attempts.gate_context_hash` not null khi runtime_mode=live.
- `order_state_transitions.transition_hash` unique để idempotent event processing.
- `reconciliation_queue_items` cần `leased_by`, `leased_until`, `deadline_at`, `dead_letter_reason`.

Nếu các field đã có trong migration, CI cần assert bằng introspection thay vì chỉ grep.

---

### 4.7 `apps/api/app/services/`

| File | Vai trò | Nhận xét | Patch |
|---|---|---|---|
| `bot_service.py` | Compose runtime, hooks DB/events | Quá lớn, nhiều callback và event handling | Tách `RuntimeHookFactory`, `LedgerEventProcessor`, `BotLifecycleService` |
| `order_ledger_service.py` | Ledger/projection/recon queue | Đúng hướng | Cần transaction atomic cho intent/receipt/projection/queue |
| `safety_ledger.py` | Timeline, attempts, receipts, incidents | Tốt cho operator | Cần immutable audit hash chain |
| `daily_trading_state.py` | Daily state từ broker equity | Tốt: recompute từ broker equity | Cần trading day theo timezone/broker server day, không chỉ `date.today()` |
| `daily_profit_lock_engine.py` | Lock engine | Cần đảm bảo là source duy nhất cho daily lock | Unify với pre-gate target resolve |
| `daily_lock_runtime_controller.py` | Apply lock action | Có close_all_and_stop, reduce_risk_only | Cần exactly-once action log + retry/compensation |
| `live_start_preflight.py` | Preflight trước live | Tốt: broker health, policy, daily state, unknown orders, incidents | Thêm broker capability proof + clock drift + market status |
| `live_readiness_guard.py` | Provider readiness | Cần hard assert capability matrix | Không chỉ health string |
| `policy_service.py` | Approval control plane | Tốt | Cần policy hash/version immutable |
| `reconciliation_queue_service.py` | Unknown queue | Tốt | Cần outbox + worker lease metrics |
| `reconciliation_lease_service.py` | Lease worker | Tốt | Cần stuck lease alert |
| `incident_notifier.py` | Notify incident | Cần multi-channel critical alert | Telegram/email/webhook integration |

**P0 trong `daily_trading_state.py`:** dùng `date.today()` theo server timezone. Live Forex/Crypto cần trading day theo broker server hoặc configured timezone. Nếu server ở UTC/VN nhưng broker day roll khác, daily TP/loss sẽ sai.

**Patch:** `TradingDayResolver`:

```python
resolve_trading_day(now_utc, broker_server_time, account_timezone, rollover_hour)
```

---

### 4.8 `apps/api/app/routers/`

| Router | Vai trò | Đề xuất |
|---|---|---|
| `live_trading.py` | timeline, dashboard, receipts, daily state, incidents, reconcile, kill switch | Thêm approval workflow cho reset lock/kill switch/reconcile/close all |
| `risk_policy.py` | Policy control | Thêm diff view, hash, approval signer, rollback |
| `broker_connections.py` | Broker credentials | Cần test connection + capability proof + encrypted secret rotation |
| `bots.py` | Bot lifecycle | Live start phải gọi `run_live_start_preflight` bắt buộc |
| `experiments.py` | Experiment registry | Live không cho experiment tự promote |
| `qa_parity.py` | QA parity | Giữ tốt cho safety |
| `ws.py` | Realtime | Cần stream order state, lock, incidents, recon queue |

**Patch P0 API:** mọi action nguy hiểm phải có:

```text
request_id
operator_id
reason
approval_status
before_snapshot_hash
after_snapshot_hash
audit_log_id
```

---

### 4.9 `apps/web/` và `apps/admin/`

Đã có các trang live quan trọng:

- `live-control-center`
- `live-orders`
- `runtime-control`
- `trading-brain`
- `broker-connections`
- component `DailyLockPanel`
- `ExecutionReceiptDrawer`
- `LiveReadinessPanel`
- `ReconciliationTimeline`
- `UnknownOrdersPanel`

**Thiếu để thành operator console thật:**

1. Nút Start Live phải hiển thị preflight checklist và không enable nếu có failed check.
2. Unknown order phải có timeline: submitted → unknown → retry n → resolved/dead-letter.
3. Daily Lock phải hiển thị target, starting equity, current equity, timezone/trading day, action result.
4. Execution receipt drawer phải hiển thị raw response hash, broker order id, client order id, latency, server time.
5. Risk panel trước mỗi lệnh: lot, max loss, margin after, exposure, spread, slippage, policy version.
6. Critical incident panel: require acknowledge/resolve reason.

**Lưu ý theo preference của người dùng:** frontend nên Việt hóa 100% nếu đây là sản phẩm nội bộ của bạn. Hiện file/page vẫn chủ yếu tiếng Anh theo tên route/component. Có thể giữ route tiếng Anh nhưng UI text nên chuyển sang tiếng Việt.

---

### 4.10 Legacy `backend/` và `frontend/`

Repo vẫn còn legacy:

- `backend/main.py`
- `backend/engine/*`
- `backend/api/trading_brain_routes.py`
- `frontend/app.py`

Trong legacy có fallback/mock/stub nhiều, ví dụ fallback sang mock data, cTrader provider legacy có pass/fallback. Dù có `README_DEPRECATED.md` và verify scripts, việc để code này trong repo production vẫn tạo rủi ro import nhầm hoặc Docker copy nhầm.

**Patch P0:**

- Di chuyển `backend/`, `frontend/` vào `legacy/archived/` hoặc xóa khỏi production branch.
- CI block nếu `apps/api` hoặc `services/*` import từ `backend.*`.
- Docker production không được `COPY backend` hoặc `COPY frontend`.
- Release workflow phải chạy `verify_production_no_legacy_stack.sh` bắt buộc.

---

### 4.11 CI / `.github/scripts`

Điểm mạnh: đã có nhiều script kiểm tra safety:

- `verify_live_no_stub.sh`
- `verify_no_live_stub_provider.py`
- `verify_live_no_fallback_spec.sh`
- `verify_live_import_boundary.py`
- `verify_broker_gate_wiring.py`
- `verify_market_data_quality_scenarios.sh`
- `verify_production_no_legacy_stack.sh`
- `verify_alembic_single_head.py`
- `verify_runtime_snapshot_payload.py`

**Thiếu:**

- Chưa chắc tất cả script này chạy bắt buộc trong `release.yml`.
- Nhiều script grep-based, dễ pass giả.
- Cần integration test Postgres thật + migration thật + live provider fake contract test.

**Patch CI P0:**

```text
ci-live-safety:
  - alembic upgrade head on Postgres
  - run unit tests trading-core/execution-service/apps-api
  - run provider capability contract tests
  - run no legacy import test
  - run no live fallback test
  - run unknown order lifecycle e2e
  - run daily lock close_all_and_stop e2e with fake broker
```

---

## 5. Danh sách lỗi/rủi ro P0 cần xử lý ngay

### P0.1 Broker live contract chưa đủ bằng chứng thật

Hiện provider wrap engine và detect method. Live trading cần broker adapter thật, không chỉ method tồn tại.

**Fix:** `BrokerCapabilityProofService` chạy khi connect:

- account authorized
- account_id match
- quote real-time
- server time valid
- instrument spec valid
- margin estimate valid
- client_order_id roundtrip supported
- order lookup supported
- execution/deal lookup supported
- close_all supported

Fail một check → không cho start live.

---

### P0.2 `supports_client_order_id=True` ở cTrader có thể quá lạc quan

`CTraderProvider.supports_client_order_id` trả True, nhưng adapter chỉ truyền comment/client_order_id nếu underlying method có parameter. Nếu method không hỗ trợ thật, idempotency lookup sẽ sai.

**Fix:** capability proof phải submit/lookup qua dry-run/sandbox hoặc verify API metadata. Nếu không có proof, `supports_client_order_id=False`.

---

### P0.3 Daily lock chưa exactly-once

`DailyLockRuntimeController` có action, nhưng cần action log + retry. Nếu close_all fail một nửa, hệ phải biết còn position nào, retry ra sao, lock trạng thái thế nào.

**Fix:** bảng `daily_lock_actions`:

```text
id, bot_instance_id, trading_day, lock_reason, lock_action,
status, attempts, last_error, requested_at, completed_at,
positions_before, positions_after, action_hash
```

Controller chỉ chạy action nếu chưa completed; action idempotent.

---

### P0.4 Order lifecycle chưa đủ atomic trước broker call

Nếu hệ chết sau khi gọi broker nhưng trước khi persist receipt, unknown order có thể không vào queue.

**Fix:** trước broker call phải persist `SUBMITTING` attempt trong transaction. Sau broker call cập nhật. Nếu timeout → `UNKNOWN` + recon queue.

---

### P0.5 Live risk context còn approximation

Exposure/correlation còn đơn giản. Với account nhỏ như 500$, sai pip value/margin/slippage có thể cháy nhanh.

**Fix:** mọi live risk dùng broker-native:

- pip/tick value từ broker
- contract size từ broker
- margin estimate từ broker
- quote bid/ask real-time
- commission/slippage model
- account currency conversion

---

### P0.6 Trading day dùng `date.today()`

Daily TP/loss sẽ sai nếu timezone server khác broker/account rollover.

**Fix:** `TradingDayResolver` theo broker server time + account timezone + configured rollover.

---

### P0.7 Ghost broker position mới tạo incident, chưa chắc pause thật

`ReconciliationWorker` tạo incident khi broker có position DB không biết. Nhưng cần runtime pause/kill switch thật.

**Fix:** incident hook phải gọi `registry.pause_new_orders(bot_id)` hoặc set daily lock/kill switch ngay, rồi ghi action result.

---

### P0.8 Legacy backend còn nhiều fallback/mock

Dù production guard có tồn tại, code legacy vẫn có thể bị import/compose nhầm.

**Fix:** remove khỏi production path, chỉ giữ archive ngoài runtime. CI import-boundary fail nếu import legacy.

---

## 6. Roadmap hoàn thiện toàn bộ module + engine

### Phase 1 — Live Safety Closure Patch

Mục tiêu: không cho bất kỳ live order nào đi qua nếu thiếu proof.

Files chính:

- `services/execution-service/execution_service/providers/base.py`
- `services/execution-service/execution_service/providers/ctrader_live.py`
- `services/execution-service/execution_service/providers/ctrader.py`
- `apps/api/app/services/live_readiness_guard.py`
- `apps/api/app/services/live_start_preflight.py`
- `.github/scripts/verify_live_no_stub.sh`

Việc cần làm:

1. Thêm `BrokerCapabilityProof` dataclass.
2. Provider live phải expose `get_capability_proof()`.
3. Preflight require proof all true.
4. CI test fake live provider thiếu capability → fail.

---

### Phase 2 — Atomic Order Ledger Patch

Files:

- `apps/api/app/services/order_ledger_service.py`
- `apps/api/app/services/safety_ledger.py`
- `services/execution-service/execution_service/execution_engine.py`
- `services/execution-service/execution_service/order_state_machine.py`
- migrations mới `0018_atomic_order_lifecycle.py`

Việc cần làm:

1. Thêm state `SUBMITTING`, `ACK_TIMEOUT`, `POSITION_VERIFY_PENDING`.
2. Persist `SUBMITTING` trước broker call.
3. Receipt/projection/recon queue cùng transaction.
4. Idempotent event hash cho mỗi lifecycle event.

---

### Phase 3 — Broker-Native Risk Patch

Files:

- `services/trading-core/trading_core/risk/instrument_spec.py`
- `services/trading-core/trading_core/risk/risk_context_builder.py`
- `services/trading-core/trading_core/risk/broker_native_risk_context.py`
- `services/trading-core/trading_core/runtime/bot_runtime.py`
- broker providers

Việc cần làm:

1. Chuẩn hóa `BrokerNativeInstrumentSpec`.
2. Live reject nếu spec source không phải broker.
3. Quote timestamp/slippage bind vào frozen hash.
4. Exposure tính theo account currency và conversion rate.

---

### Phase 4 — Daily TP/Loss Runtime Lock Patch

Files:

- `apps/api/app/services/daily_profit_lock_engine.py`
- `apps/api/app/services/daily_lock_runtime_controller.py`
- `apps/api/app/services/daily_trading_state.py`
- `services/trading-core/trading_core/runtime/runtime_registry.py`
- migration `0019_daily_lock_actions.py`

Việc cần làm:

1. Thêm `TradingDayResolver`.
2. Thêm `daily_lock_actions` exactly-once.
3. Khi TP/loss hit: pause new orders ngay.
4. Nếu action `close_all_and_stop`: close all, verify remaining=0, stop runtime.
5. Nếu fail: critical incident + keep locked.

---

### Phase 5 — Unknown Order & Reconciliation Hardening

Files:

- `services/execution-service/execution_service/unknown_order_reconciler.py`
- `services/execution-service/execution_service/reconciliation_worker.py`
- `apps/api/app/workers/reconciliation_daemon.py`
- `apps/api/app/services/reconciliation_queue_service.py`
- `apps/api/app/services/reconciliation_lease_service.py`

Việc cần làm:

1. One source of truth: queue daemon hoặc runtime worker, không để hai hệ xử lý lệch nhau.
2. Unknown > 30s retry; > 5 phút hoặc 3 fail: dead-letter + daily lock + critical incident.
3. Direct broker lookup by client id bắt buộc live.
4. Partial fill handling.
5. Ghost position → pause live immediately.

---

### Phase 6 — Operator Control Center Patch

Files:

- `apps/api/app/routers/live_trading.py`
- `apps/web/app/(app)/live-control-center/page.tsx`
- `apps/web/components/live/*`
- `apps/admin/app/operations-dashboard/page.tsx`

Việc cần làm:

1. Preflight checklist UI.
2. Daily lock action status.
3. Unknown order timeline.
4. Critical incident acknowledge/resolve with reason.
5. Risk snapshot drawer.
6. Live start button disabled until all checks pass.
7. Việt hóa UI nếu dùng cho người Việt.

---

### Phase 7 — CI/Release Gate Patch

Files:

- `.github/workflows/release.yml`
- `.github/workflows/services-ci.yml`
- `.github/scripts/*`

Việc cần làm:

1. Mandatory Postgres migration test.
2. Mandatory no legacy import.
3. Mandatory no live fallback/stub.
4. Unknown order E2E.
5. Daily lock E2E.
6. Broker capability contract test.
7. Artifact report upload.

---

## 7. Production live trading acceptance checklist

Chỉ cho live tiền thật khi pass 100%:

### Broker

- [ ] Provider live không wrap mock/legacy.
- [ ] Account ID broker khớp DB.
- [ ] Client order id roundtrip proof.
- [ ] Order lookup by client id pass.
- [ ] Execution/deal lookup pass.
- [ ] Broker quote real-time pass.
- [ ] Broker server time drift < policy.
- [ ] Margin estimate broker-native pass.
- [ ] Close all positions tested.

### Risk

- [ ] Live không dùng fallback instrument spec.
- [ ] SL bắt buộc.
- [ ] Max loss if SL hit <= policy.
- [ ] Margin after order pass.
- [ ] Spread/slippage pass.
- [ ] Exposure/correlation pass.
- [ ] Daily TP/loss state fresh.

### Order lifecycle

- [ ] Idempotency reservation trước broker call.
- [ ] SUBMITTING persisted trước broker call.
- [ ] Receipt valid mới mở trade.
- [ ] UNKNOWN vào recon queue ngay.
- [ ] Reconciliation worker/daemon chạy.
- [ ] Ghost position lock/pause ngay.

### Operator

- [ ] Start live cần preflight pass.
- [ ] Kill switch hoạt động.
- [ ] Reset lock cần admin + reason.
- [ ] Critical incident hiển thị realtime.
- [ ] Unknown orders có timeline.
- [ ] Daily lock action có audit.

### CI

- [ ] Alembic single head.
- [ ] Upgrade DB clean.
- [ ] No legacy production import.
- [ ] No live stub provider.
- [ ] No live fallback spec.
- [ ] E2E daily lock.
- [ ] E2E unknown order.

---

## 8. Thứ tự patch mạnh nhất nên làm tiếp

### Patch tiếp theo nên làm ngay

**LIVE BROKER CAPABILITY PROOF + ATOMIC SUBMITTING LEDGER PATCH**

Lý do: đây là điểm quyết định hệ có an toàn để gọi broker thật hay không. Nếu chưa có proof và chưa ghi `SUBMITTING` trước broker call, mọi engine khác dù tốt vẫn có thể mất kiểm soát khi timeout/network crash.

Scope:

1. Thêm `BrokerCapabilityProof` vào `providers/base.py`.
2. Implement proof cho `CTraderLiveProvider`.
3. `LiveReadinessGuard` require proof.
4. `ExecutionEngine.place_order()` gọi ledger hook `mark_submitting()` trước broker call.
5. Nếu broker timeout → `UNKNOWN` + queue.
6. Test: broker submit timeout sau khi nhận order → DB phải có UNKNOWN + recon item.

---

## 9. Kết luận cuối

`forex-main-5(2)` không còn là demo đơn giản; nó đã có khung của một live trading platform thật. Nhưng để chạy tiền thật, cần đóng các lỗ P0 sau:

1. Broker live capability phải có proof thật, không chỉ class/method tồn tại.
2. Order ledger phải ghi `SUBMITTING` trước broker call và xử lý UNKNOWN atomic.
3. Daily lock phải exactly-once và có runtime action log.
4. Risk context live phải broker-native hoàn toàn.
5. Reconciliation phải có quyền pause/lock runtime thật khi ghost/unknown.
6. Legacy/mock/fallback phải bị loại khỏi production path.
7. Operator UI phải cho thấy preflight, risk, receipt, unknown, incident, daily lock đầy đủ.

**Trạng thái đề xuất:** chưa bật live tiền thật; chỉ cho chạy paper/demo/sandbox. Sau khi hoàn thành Phase 1 + Phase 2 + Phase 3 + Phase 4, có thể chạy live với vốn nhỏ dưới giám sát và kill switch bật.
