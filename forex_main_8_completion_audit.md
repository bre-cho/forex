# FOREX-MAIN-8 — LIVE TRADING COMPLETION AUDIT & PATCH PLAN

**Mục tiêu audit:** nghiên cứu bản `forex-main-8.zip` để đánh giá mức độ sẵn sàng chạy live trading thật cho Forex/Crypto, rà từng module/engine quan trọng, phát hiện điểm còn thiếu/rủi ro, và đề xuất kế hoạch hoàn thiện theo thứ tự P0/P1/P2.

> Lưu ý an toàn: không có hệ bot nào có thể đảm bảo lợi nhuận cố định. Mục tiêu production ở đây là **giảm lỗi kỹ thuật, chặn giao dịch sai, bảo vệ vốn, có kiểm toán, có rollback, có live preflight và có kill switch**, không phải cam kết lợi nhuận.

---

## 1. Kết luận nhanh

Bản `forex-main-8` **tiến bộ rõ so với bản 7**. Các mảng quan trọng đã được bổ sung:

- Daily TP lock engine: `apps/api/app/services/daily_profit_lock_engine.py`
- Daily trading state: `apps/api/app/services/daily_trading_state.py`
- Live readiness guard: `apps/api/app/services/live_readiness_guard.py`
- Live start preflight: `apps/api/app/services/live_start_preflight.py`
- Order state machine: `services/execution-service/execution_service/order_state_machine.py`
- Execution receipt + order attempt ledger: `apps/api/app/services/safety_ledger.py`
- Risk context builder: `services/trading-core/trading_core/risk/risk_context_builder.py`
- Pre-execution gate: `services/trading-core/trading_core/runtime/pre_execution_gate.py`
- Reconciliation worker: `services/execution-service/execution_service/reconciliation_worker.py`
- CI guard scripts: `.github/scripts/verify_live_no_stub.sh`, `verify_broker_gate_wiring.py`, `verify_production_no_legacy_stack.sh`

**Nhưng chưa nên chạy tiền thật ngay.** Lý do: còn các điểm P0 có thể gây lỗi runtime hoặc quyết định sai trong live path:

1. `bot_service.py` dùng `DailyTradingState` nhưng không import trực tiếp → có nguy cơ `NameError` khi gọi `get_portfolio_risk_snapshot()`.
2. Live preflight yêu cầu daily state fresh trong 60 giây, nhưng chưa thấy bước bắt buộc sync equity từ broker ngay trước start → live start có thể bị block giả hoặc phụ thuộc state cũ.
3. Daily TP policy đang có **2 schema khác nhau**: nested `daily_take_profit` trong `DailyProfitLockEngine`, nhưng `PreExecutionGate` đọc dạng flat `daily_take_profit_mode`, `daily_take_profit_amount`, `daily_take_profit_pct`. Điều này có thể làm Daily TP không đồng nhất giữa DB policy và runtime gate.
4. Risk context builder đang dùng công thức margin/notional proxy quá đơn giản (`notional * 0.01`, pip value fixed) → chưa đủ an toàn cho Forex đa cặp, crypto perpetual, leverage/account currency khác nhau.
5. `on_order()` vẫn ghi `Order` trực tiếp theo payload đơn giản, trong khi ledger/order_attempt mới là source-of-truth. Có nguy cơ dữ liệu order table và execution receipt lệch nhau.
6. Reconciliation mới auto-close stale DB trade và cảnh báo ghost broker position, nhưng chưa đủ flow “unknown order → reconcile broker order/deal/position → repair attempt/receipt/trade” theo idempotency key.
7. Legacy stack `backend/` và `frontend/` vẫn tồn tại. Production config đã có script chặn, nhưng cần hard policy để không import nhầm hoặc deploy nhầm.
8. Broker providers `MT5/Bybit/cTrader` đã có hardening nhưng vẫn cần live sandbox acceptance test với broker thật trước khi mở `mode=live`.

**Đánh giá readiness:**

| Nhóm | Điểm | Nhận xét |
|---|---:|---|
| Kiến trúc monorepo | 8/10 | Tách `apps/api`, `apps/web`, `services/trading-core`, `execution-service` tốt |
| Risk gate | 7/10 | Có nhiều gate nhưng policy schema chưa thống nhất |
| Daily TP/Daily loss | 7/10 | Đã có lock engine, cần unify + operator reset + start sync |
| Broker execution | 6.5/10 | Có receipt/timeout, nhưng reconciliation chưa đủ broker-source-of-truth |
| Order lifecycle | 7/10 | Có state machine, nhưng `Order` table và ledger còn song song |
| Live preflight | 6.5/10 | Có guard, nhưng daily state freshness dễ lỗi giả |
| Frontend/operator | 6/10 | Có live center/runtime pages, cần policy UI, reset lock, incident drilldown |
| CI/QA | 7/10 | Có verify scripts, cần e2e live-simulation and migration contract tests |
| Production readiness | 6.5/10 | Chưa đủ để chạy tiền thật |

---

## 2. Cấu trúc repo chính

### 2.1 Apps

- `apps/api`: FastAPI backend chính, SQLAlchemy async, Alembic, auth, bots, broker connections, risk policy, runtime readiness, safety ledger.
- `apps/web`: Next.js frontend dashboard/operator UI.
- `apps/admin`: admin shell đơn giản.

### 2.2 Services

- `services/trading-core`: runtime bot, strategy engines, risk gate, market data quality, position sizing, unified trade pipeline.
- `services/execution-service`: broker provider abstraction, order router, execution engine, order state machine, reconciliation worker.
- `services/signal-service`: signal feed/scoring/broadcast.
- `services/analytics-service`: drawdown, equity curve, expectancy, profit factor, Sharpe.
- `services/notification-service`: dispatch notification.
- `services/billing-service`: plan/entitlement/Stripe.

### 2.3 Legacy

- `backend/`: legacy FastAPI-ish stack with many old engines.
- `frontend/`: legacy Streamlit app.

**Khuyến nghị:** giữ legacy chỉ để tham khảo, nhưng production phải chỉ deploy `apps/api` + `apps/web` + services.

---

## 3. Những phần đã tốt ở bản 8

### 3.1 Live fail-closed đã rõ hơn

Có guard ở nhiều lớp:

- `LiveReadinessGuard.BAD_PROVIDER_MODES = {stub, paper, unavailable, degraded}`
- `BotRuntime._ensure_provider_usable()` block live nếu provider mode là `stub/paper/degraded/unavailable`
- `ExecutionEngine.place_order()` block raw `OrderRequest` trong live, bắt buộc dùng `ExecutionCommand`
- `.github/scripts/verify_live_no_stub.sh` kiểm tra live không rơi vào stub/paper

Đây là hướng đúng.

### 3.2 Order state machine đã xuất hiện

`services/execution-service/execution_service/order_state_machine.py` định nghĩa state:

```text
INTENT_CREATED → GATE_ALLOWED / GATE_BLOCKED
GATE_ALLOWED → RESERVED
RESERVED → SUBMITTED
SUBMITTED → ACKED / FILLED / PARTIAL / REJECTED / UNKNOWN
UNKNOWN → RECONCILING
RECONCILING → FILLED / REJECTED / FAILED_NEEDS_OPERATOR
FILLED → OPEN_POSITION_VERIFIED / CLOSED
```

Đây là nền tảng bắt buộc cho live trading.

### 3.3 Daily TP lock engine đã có

`DailyProfitLockEngine.evaluate_and_apply()`:

- sync equity vào `DailyTradingState`
- load active policy
- resolve target theo `daily_take_profit`
- set `state.locked = True`
- ghi `daily_lock_events`
- publish/incident event `daily_tp_hit`

Điểm này sửa đúng yêu cầu trước đó: “Daily loss đã có, Daily TP chưa có”.

### 3.4 Execution receipt đã tốt hơn

`ExecutionEngine.place_order()` đã có:

- idempotency verifier
- broker submit timeout
- `submit_status`, `fill_status`
- raw response latency
- block nếu thiếu `brain_cycle_id`, `idempotency_key`, `pre_execution_context`

### 3.5 Reconciliation worker đã có nền

Worker đã so sánh:

- DB open trades
- broker open positions
- stale DB trade → auto close
- ghost broker position → mismatch incident
- persistent mismatch → critical incident + escalation action

Đây là bước đúng, nhưng chưa đủ cho unknown order recovery.

---

## 4. P0 blockers cần sửa trước khi live

### P0-1 — Fix import bug trong `bot_service.py`

**Vấn đề:** `get_portfolio_risk_snapshot()` dùng `DailyTradingState` nhưng import đầu file chưa có model này.

Hiện import có:

```py
from app.models import (
    BotInstance,
    BotInstanceConfig,
    BotRuntimeSnapshot,
    BrokerConnection,
    Order,
    Signal,
    Trade,
)
```

Nhưng dòng xử lý có:

```py
select(DailyTradingState).where(...)
```

**Patch:** thêm `DailyTradingState` vào import.

```py
from app.models import (
    BotInstance,
    BotInstanceConfig,
    BotRuntimeSnapshot,
    BrokerConnection,
    DailyTradingState,
    Order,
    Signal,
    Trade,
)
```

**Test bắt buộc:**

- test `get_portfolio_risk_snapshot()` có daily state của nhiều bot.
- test live gate block khi portfolio kill switch true.

---

### P0-2 — Unify Daily TP policy schema

**Vấn đề:** hiện có 2 cách đọc policy.

`DailyProfitLockEngine` đọc nested:

```json
{
  "daily_take_profit": {
    "enabled": true,
    "mode": "fixed_amount",
    "daily_take_profit_amount": 25,
    "after_hit_action": "stop_new_orders"
  }
}
```

`PreExecutionGate` lại đọc flat:

```json
{
  "daily_take_profit_mode": "fixed_amount",
  "daily_take_profit_amount": 25
}
```

**Rủi ro:** API policy active đã bật Daily TP, lock engine hiểu, nhưng runtime gate có thể không hiểu hoặc dùng default `1e18` → bot vẫn đặt lệnh sau khi đạt TP nếu state chưa lock kịp.

**Patch:** tạo một resolver duy nhất trong `trading_core/risk/daily_profit_policy.py` và dùng ở cả 2 nơi.

Đề xuất chuẩn policy duy nhất:

```json
{
  "daily_take_profit": {
    "enabled": true,
    "mode": "fixed_amount | percent_equity | capital_tier",
    "amount": 25,
    "pct": 2.0,
    "tiers": [
      {"min_equity": 100, "max_equity": 500, "target_amount": 5},
      {"min_equity": 500, "max_equity": 1000, "target_amount": 15}
    ],
    "after_hit_action": "stop_new_orders | close_all_and_stop"
  }
}
```

**Backward compatibility:** resolver vẫn đọc alias cũ:

- `daily_take_profit_amount`
- `daily_take_profit_pct`
- `daily_take_profit_tiers`
- flat keys trong `gate_policy`

---

### P0-3 — Live start phải sync equity từ broker trước khi kiểm tra daily state

**Vấn đề:** `run_live_start_preflight()` gọi `daily.get_or_create()` rồi check `updated_at <= 60s`. Nếu daily state chưa được recompute từ broker ngay trước đó, live start có thể fail vì `daily_state_stale`.

**Patch:** trong `run_live_start_preflight()`, trước khi check freshness:

1. gọi `provider.get_account_info()`
2. lấy `equity`
3. gọi `daily.recompute_from_broker_equity(bot.id, equity)`
4. commit
5. check lock/daily TP/daily loss sau khi sync

Pseudo patch:

```py
info = await provider.get_account_info()
equity = float(getattr(info, "equity", 0.0) or 0.0)
if equity <= 0:
    raise LiveStartPreflightError("account_equity_invalid")
state = await daily.recompute_from_broker_equity(bot.id, equity)
await db.commit()
```

**Thêm checks:**

- nếu `state.locked=True` → block start với `daily_lock_active:<reason>`
- nếu active policy Daily TP already hit → block start
- nếu daily loss hit → block start

---

### P0-4 — RiskContextBuilder chưa đủ production

**Vấn đề:** công thức hiện tại:

```py
notional = volume * entry_price * 100000
projected_margin = margin + notional * 0.01
free_margin_after = free_margin - notional * 0.01
pip_value = pip_value_per_lot(symbol)
```

Rủi ro:

- Forex lot size khác crypto contract size.
- Account currency không phải USD.
- Leverage/margin rate không đồng nhất.
- JPY pairs pip size khác đã có nhưng pip value vẫn cần conversion.
- Crypto perpetual dùng qty/contract, không phải 100000 units.
- Correlation USD proxy quá đơn giản.

**Patch:** tạo `InstrumentSpecService` và bắt provider trả về instrument specs.

Schema đề xuất:

```py
@dataclass
class InstrumentSpec:
    symbol: str
    asset_class: str  # forex | crypto | metal | index
    contract_size: float
    pip_size: float
    tick_size: float
    tick_value: float | None
    min_volume: float
    volume_step: float
    margin_rate: float
    quote_currency: str
    base_currency: str | None
```

`RiskContextBuilder.build()` phải dùng:

- `contract_size`
- `margin_rate`
- conversion quote/account currency
- broker-reported margin estimate nếu provider hỗ trợ
- real open positions from broker
- real pending orders nếu có

**Provider interface cần thêm:**

```py
async def get_instrument_spec(symbol: str) -> InstrumentSpec
async def estimate_order_margin(request: OrderRequest) -> float
async def get_pending_orders() -> list[dict]
```

---

### P0-5 — `Order` table không nên là source-of-truth song song

**Vấn đề:** `on_order()` vẫn tạo row `Order` đơn giản với status `pending/filled/rejected`. Trong khi hệ mới đã có:

- `broker_order_attempts`
- `order_state_transitions`
- `broker_execution_receipts`
- `broker_order_events`

**Rủi ro:** dashboard đọc `orders` có thể khác ledger thật. Live operator nhìn sai trạng thái.

**Patch:**

- Biến `Order` table thành projection/read-model từ ledger.
- `on_order()` không tự tạo order theo payload thô nếu chưa có `idempotency_key`/receipt.
- Tạo service `OrderProjectionService`:
  - `upsert_from_order_attempt()`
  - `upsert_from_execution_receipt()`
  - `sync_order_status_from_state_transition()`

**Rule:** ledger là source-of-truth; `orders` chỉ là projection cho UI.

---

### P0-6 — Unknown order reconciliation chưa đủ

**Vấn đề:** khi broker timeout hoặc lỗi unknown, runtime emit `order_unknown`, nhưng reconciliation worker hiện chủ yếu so DB open trades vs broker positions. Nó chưa có flow:

```text
UNKNOWN order attempt
→ query broker order by client/idempotency/comment/orderLinkId
→ query executions/deals
→ query positions
→ classify FILLED / REJECTED / STILL_UNKNOWN
→ update receipt + attempt + transitions
```

**Patch:** thêm `UnknownOrderReconciler`.

Provider cần có:

```py
async def get_order_by_client_id(client_order_id: str) -> dict | None
async def get_executions_by_client_id(client_order_id: str) -> list[dict]
async def get_position_by_order_id(order_id: str) -> dict | None
```

Flow:

1. lấy `broker_order_attempts.current_state == UNKNOWN`
2. query broker theo `idempotency_key/comment/orderLinkId`
3. nếu found execution → `RECONCILING → FILLED → OPEN_POSITION_VERIFIED`
4. nếu broker reject/cancel → `RECONCILING → REJECTED`
5. nếu quá N vòng vẫn unknown → `FAILED_NEEDS_OPERATOR` + kill switch

---

### P0-7 — Live broker acceptance test bắt buộc

Repo có `test_live_ctrader_smoke.py`, nhưng cần một bộ acceptance chuẩn không đặt lệnh thật mặc định.

**Cần 3 tầng test:**

1. **Read-only live smoke**
   - connect broker
   - get account info
   - get candles
   - get instrument spec
   - get open positions
   - no order placement

2. **Demo/sandbox order smoke**
   - place micro order
   - verify ACK/FILL/PENDING
   - close position
   - verify trade history
   - verify receipt persisted

3. **Live dry-run preflight**
   - same live account, but no order
   - verify all gates using simulated request
   - validate margin/exposure/daily TP/daily loss

**Hard rule:** `LIVE_ORDER_TEST=true` mới cho phép đặt lệnh thật, và chỉ micro lot.

---

## 5. P1 hoàn thiện operator/control plane

### P1-1 — Daily TP operator UI

Cần UI trong `apps/web` cho:

- xem `daily_profit_amount`, `target`, `starting_equity`, `current_equity`
- trạng thái `locked`, `lock_reason`
- nút `Reset daily lock` có confirm + audit
- nút `Close all and stop` nếu policy after_hit_action yêu cầu
- timeline `daily_lock_events`

API cần thêm:

```text
GET  /v1/workspaces/{workspace_id}/bots/{bot_id}/daily-state
POST /v1/workspaces/{workspace_id}/bots/{bot_id}/daily-state/reset-lock
POST /v1/workspaces/{workspace_id}/bots/{bot_id}/daily-state/sync-from-broker
GET  /v1/workspaces/{workspace_id}/bots/{bot_id}/daily-lock-events
```

### P1-2 — Risk policy builder UI

Hiện có API `risk-policy`, nhưng frontend cần form rõ:

- max daily loss %
- daily TP mode/amount/tier
- max margin usage
- max account exposure
- max symbol exposure
- max correlated exposure
- max open positions
- max spread/slippage
- news/session blackout
- policy version diff
- approve/activate flow

### P1-3 — Incident center nâng cấp

Cần incident center cho:

- `order_unknown`
- `reconciliation_mismatch_persists`
- `daily_tp_hit`
- `daily_loss_limit_hit`
- `provider_unhealthy`
- `market_data_stale`

Mỗi incident cần:

- severity
- current lock impact
- recommended action
- related bot/order/receipt/trade
- resolve action with audit

### P1-4 — Runtime snapshot contract versioning

`runtime snapshot` nên có contract version:

```json
{
  "contract_version": "runtime_snapshot.v1",
  "status": "running",
  "provider": {...},
  "risk": {...},
  "daily": {...},
  "orders": {...},
  "incidents": {...}
}
```

CI cần block nếu frontend dùng field không có trong backend contract.

---

## 6. P2 nâng cấp thành “AI trading system” đúng nghĩa

### P2-1 — Strategy lifecycle governance

Cần tách rõ:

```text
research → backtest → walk-forward → paper → demo → live-canary → live-scale
```

Mỗi stage có gate:

- min sample size
- max drawdown
- profit factor
- Sharpe/Sortino
- slippage stress
- spread stress
- news blackout stress
- out-of-sample pass

Không cho strategy đi live nếu chưa qua stage.

### P2-2 — Position sizing engine production

Cần engine sizing chung:

- fixed fractional risk
- volatility adjusted size
- max lot by margin
- max loss if SL hit
- min/max broker volume step
- scale-down when drawdown increases
- disable martingale by default

### P2-3 — Market data quality engine

Đã có tests `test_market_data_quality.py`, cần production hóa:

- stale candle detection
- gap detection
- spread spike detection
- duplicate timestamp
- broker server time drift
- volume abnormality
- cross-source confirmation optional

### P2-4 — Real broker adapters hoàn chỉnh

Cần provider maturity matrix:

| Provider | Read-only | Demo order | Live order | Reconcile | Instrument spec | Status |
|---|---:|---:|---:|---:|---:|---|
| Paper | ✅ | ✅ | N/A | basic | fake | OK dev |
| cTrader | partial | cần test | chưa approve | partial | missing | P0/P1 |
| MT5 | partial | cần test | chưa approve | partial | missing | P0/P1 |
| Bybit | better | cần testnet smoke | chưa approve | partial | partial | P0/P1 |

---

## 7. Patch order đề xuất

### Phase 0 — Build must not break

1. Add missing imports and runtime unit tests.
2. Run AST/compile check for all Python files.
3. Run Alembic single-head verifier.
4. Run no-legacy-production verifier.

### Phase 1 — Daily TP finalization

1. Create unified `DailyTakeProfitPolicy` resolver.
2. Patch `PreExecutionGate` to use resolver.
3. Patch `DailyProfitLockEngine` to use same resolver.
4. Add API endpoints for sync/reset/events.
5. Add frontend Daily TP control panel.
6. Add tests for fixed, percent, tier modes.

### Phase 2 — Live preflight hardening

1. Sync broker equity before daily freshness check.
2. Block if daily lock active.
3. Block if active policy missing.
4. Block if critical incident open.
5. Block if broker account/instrument invalid.
6. Add dry-run preflight endpoint.

### Phase 3 — Risk context production

1. Add `InstrumentSpec` dataclass.
2. Add provider methods for spec/margin estimate.
3. Patch `RiskContextBuilder`.
4. Add tests for EURUSD, USDJPY, XAUUSD, BTCUSDT.
5. Add account currency conversion support.

### Phase 4 — Order ledger source-of-truth

1. Add `OrderProjectionService`.
2. Make `orders` table projection only.
3. Ensure every broker result writes receipt.
4. Ensure every receipt updates attempt + transition.
5. Add UI that reads source status from ledger.

### Phase 5 — Unknown order recovery

1. Add provider lookup by client/idempotency key.
2. Add `UnknownOrderReconciler`.
3. Add state transition `UNKNOWN → RECONCILING → FILLED/REJECTED/FAILED_NEEDS_OPERATOR` tests.
4. Add incident auto escalation if still unknown.

### Phase 6 — Broker acceptance pack

1. Read-only broker smoke.
2. Demo order smoke.
3. Live dry-run no-order smoke.
4. Optional micro live order guarded by env flags.

---

## 8. Checklist “được phép live”

Chỉ bật live khi toàn bộ đạt:

- [ ] Không còn import/runtime error trong `apps/api/app` và `services/*`.
- [ ] Alembic single head pass.
- [ ] DB migration từ clean database pass.
- [ ] `verify_live_no_stub.sh` pass.
- [ ] `verify_broker_gate_wiring.py` pass.
- [ ] `verify_production_no_legacy_stack.sh` pass.
- [ ] Daily TP fixed/percent/tier pass.
- [ ] Daily loss lock pass.
- [ ] Policy approve/activate required for live pass.
- [ ] Live start preflight syncs broker equity.
- [ ] Provider read-only smoke pass.
- [ ] Demo order smoke pass.
- [ ] Unknown order reconciliation pass.
- [ ] Reconciliation mismatch escalates kill switch.
- [ ] Operator UI shows runtime/provider/daily/order/incident truth.
- [ ] Manual kill switch tested.
- [ ] Emergency stop tested.
- [ ] All broker credentials encrypted.
- [ ] No legacy `backend/` or `frontend/` in production compose/release.

---

## 9. File-by-file patch map

### `apps/api/app/services/bot_service.py`

**Sửa:**

- import `DailyTradingState`
- replace raw `on_order` projection with `OrderProjectionService`
- hard fail live if ledger write fails
- improve `get_portfolio_risk_snapshot()` performance and tests

### `apps/api/app/services/live_start_preflight.py`

**Sửa:**

- sync broker equity first
- block active daily lock
- include active policy snapshot hash/version
- include provider account/instrument preflight

### `apps/api/app/services/daily_profit_lock_engine.py`

**Sửa:**

- use unified resolver
- support `close_all_and_stop`
- emit operator action
- avoid double lock events on repeated evaluation

### `services/trading-core/trading_core/runtime/pre_execution_gate.py`

**Sửa:**

- use nested Daily TP policy resolver
- include `daily_locked` explicit check
- include max risk amount if SL hit
- include min RR after spread/slippage

### `services/trading-core/trading_core/risk/risk_context_builder.py`

**Sửa:**

- use instrument spec
- use broker margin estimate
- include pending orders
- convert pip/tick value to account currency
- compute `max_loss_amount_if_sl_hit` accurately

### `services/execution-service/execution_service/execution_engine.py`

**Sửa:**

- include `submit_status/fill_status` for every reject path
- standardize receipt construction
- classify timeout as `UNKNOWN` and trigger immediate reconcile callback

### `services/execution-service/execution_service/reconciliation_worker.py`

**Sửa:**

- add unknown order reconciliation
- verify positions after fill
- create/update missing DB trade from broker position if safe
- lock bot on ghost broker position until operator resolves

### `services/execution-service/execution_service/providers/*`

**Sửa:**

- add instrument spec
- add order lookup by client order id
- add execution/deal lookup
- normalize error codes
- make live mode fail closed if SDK missing or testnet key used

### `apps/web/app/(app)/live-control-center/page.tsx`

**Sửa:**

- show Daily TP/Daily loss panel
- show policy active version
- show readiness checklist
- show manual reset/kill switch controls

### `apps/web/app/(app)/live-orders/page.tsx`

**Sửa:**

- read order status from ledger/projection
- show state transition timeline
- show broker receipt/raw response drilldown
- show unknown/reconcile actions

---

## 10. Ưu tiên hành động ngay

**Bước mạnh nhất tiếp theo:** build patch `P0 LIVE SAFETY CLOSURE PATCH` gồm 6 phần:

1. Fix `DailyTradingState` import + tests.
2. Unified Daily TP resolver + gate integration.
3. Live preflight broker equity sync + daily lock block.
4. Order projection service để ledger là source-of-truth.
5. Unknown order reconciler skeleton + tests.
6. CI script `verify_live_safety_closure.sh` chạy tất cả guard bắt buộc.

Sau patch này, repo mới đủ nền để bước sang **broker demo order smoke**. Chưa nên chạy live money trước khi demo smoke và unknown order recovery pass.
