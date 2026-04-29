# FOREX-MAIN-9 — LIVE TRADING COMPLETION AUDIT

**Vai trò audit:** Technical Lead live trading bot Forex/Crypto  
**Repo:** `forex-main-9.zip`  
**Mục tiêu:** biến code thành hệ thống live trading thật, fail-closed, có ledger, preflight, risk gate, broker receipt và reconciliation đủ chuẩn production.

> Cảnh báo vận hành: trading live luôn có rủi ro mất vốn. Audit này chỉ đánh giá kỹ thuật/code readiness, không phải cam kết lợi nhuận.

---

## 1. Kết luận tổng quan

Bản `forex-main-9` **tiến bộ rõ so với bản 8**. Repo đã có thêm nhiều cấu phần đúng hướng production:

- `DailyTradingStateService` đã tách rõ daily state.
- `DailyProfitLockEngine` đã có daily take-profit lock event.
- `LiveStartPreflight` đã sync equity từ broker trước khi start live.
- `RiskContextBuilder` đã tồn tại để tính margin/exposure.
- `BrokerOrderAttempt`, `BrokerExecutionReceipt`, `OrderStateTransition`, `BrokerAccountSnapshot`, `BrokerReconciliationRun` đã có model + migration.
- `ExecutionEngine` đã chặn `OrderRequest` raw trong live mode, bắt `ExecutionCommand`, `brain_cycle_id`, `idempotency_key`, reservation verifier.
- cTrader provider đã fail-closed nếu live mà execution adapter unavailable.
- Có CI scripts như `verify_live_no_stub`, `verify_broker_gate_wiring`, `verify_live_import_boundary`.

Tuy nhiên repo **vẫn chưa nên chạy tiền thật ngay** vì còn các lỗ hổng P0:

1. **Daily TP lock chưa đóng vòng đầy đủ:** lock có ghi state/event, nhưng chưa bảo đảm runtime dừng/đóng vị thế đúng `after_hit_action`.
2. **RiskContextBuilder còn dùng công thức margin giả định `notional * 0.01`**, chưa lấy margin requirement thật theo broker/instrument/leverage.
3. **OrderProjectionService đang dùng `orders.broker_order_id` làm chỗ chứa idempotency key**, dễ phá source-of-truth khi broker_order_id thật về sau thay đổi.
4. **UnknownOrderReconciler chưa được nối chắc vào DB lifecycle cho mọi order UNKNOWN**, phần hook có nhưng cần worker scheduled + incident/runbook/operator queue.
5. **Legacy backend vẫn còn trong repo và docker-compose**, có nguy cơ drift/route nhầm nếu deploy nhầm stack.
6. **Broker support còn mỏng:** cTrader có adapter hướng đúng, MT5/Bybit vẫn có stub/paper fallback cần khóa cứng trong live.
7. **Không thấy migration parity test bắt buộc trong runtime startup**, mới có script kiểm tra nhưng chưa chắc deploy luôn chạy `alembic upgrade head` trước API.

**Live readiness hiện tại:** khoảng **72/100**. Có thể dùng cho paper/forward test nghiêm túc. Chưa đủ chuẩn auto live tiền thật.

---

## 2. Kiến trúc hiện tại

### 2.1 Các khối chính

```text
apps/api
  ├─ routers/live_trading.py
  ├─ routers/risk_policy.py
  ├─ routers/broker_connections.py
  ├─ services/bot_service.py
  ├─ services/daily_trading_state.py
  ├─ services/daily_profit_lock_engine.py
  ├─ services/live_start_preflight.py
  ├─ services/safety_ledger.py
  └─ services/order_projection_service.py

services/trading-core
  ├─ runtime/bot_runtime.py
  ├─ runtime/pre_execution_gate.py
  ├─ runtime/runtime_factory.py
  └─ risk/risk_context_builder.py

services/execution-service
  ├─ execution_engine.py
  ├─ order_state_machine.py
  ├─ reconciliation_worker.py
  ├─ unknown_order_reconciler.py
  └─ providers/{ctrader,mt5,bybit,paper}.py

backend/
  └─ legacy stack vẫn tồn tại
```

### 2.2 Luồng live trading mong muốn

```text
Signal
 → Brain decision
 → Idempotency reserve
 → PreExecutionGate
 → RiskContextBuilder
 → ExecutionCommand
 → Broker provider submit
 → BrokerExecutionReceipt
 → OrderStateTransition
 → OrderProjection read model
 → Reconciliation worker
 → Daily TP/Loss lock engine
 → Incident/operator queue
```

Repo đã có nhiều mảnh, nhưng cần khóa thành một state machine duy nhất.

---

## 3. Điểm đã hoàn thiện tốt

### 3.1 Daily state đã có service riêng

File: `apps/api/app/services/daily_trading_state.py`

Điểm tốt:

- Có `get_or_create` theo `bot_instance_id + trading_day`.
- Có `recompute_from_broker_equity` tính `daily_profit_amount` từ `current_equity - starting_equity`.
- Có `lock_day` để chặn bot sau sự kiện rủi ro.

Điểm cần sửa:

- `date.today()` dùng local date của server, không dùng timezone/account trading day. Với broker quốc tế, daily reset cần theo **broker server timezone** hoặc workspace timezone.
- `starting_equity` được set khi lần đầu sync. Nếu bot restart giữa ngày sau khi equity đã biến động, starting_equity có thể bị sai nếu daily row chưa tồn tại.

### 3.2 DailyProfitLockEngine đã xuất hiện

File: `apps/api/app/services/daily_profit_lock_engine.py`

Điểm tốt:

- Đọc active policy qua `PolicyService`.
- Dùng `resolve_daily_take_profit_target` từ `trading_core.risk.daily_profit_policy`.
- Khi đạt TP thì set `state.locked=True`, `lock_reason=daily_take_profit_hit` và ghi `daily_lock_events`.

Điểm cần sửa:

- `lock_action` mới được ghi event, chưa chắc runtime thực thi hành động tương ứng:
  - `stop_new_orders`
  - `close_all_and_stop`
  - `reduce_risk_only`
- Chưa có idempotency cho daily lock event, có thể ghi lặp nhiều lần nếu sync equity liên tục.
- Chưa có operator control rõ: reset lock cần reason/audit, nhưng chưa nối RBAC/workspace role đầy đủ ở mọi endpoint.

### 3.3 LiveStartPreflight đã sync broker equity

File: `apps/api/app/services/live_start_preflight.py`

Điểm tốt:

- Check provider health qua `LiveReadinessGuard`.
- Check active policy đã approve.
- Gọi `provider.get_account_info()` và recompute daily state trước khi start.
- Block nếu daily state stale hoặc daily lock active.
- Block nếu có critical incident đang mở.

Điểm cần sửa:

- Nếu `provider.get_account_info()` lỗi, code fallback sang `daily.get_or_create()` rồi dựa vào freshness. Trong live mode nên **fail closed ngay**, không fallback mềm.
- Chưa check account_id của broker connection khớp bot/workspace/account snapshot mới nhất ở API layer.
- Chưa check `alembic head`, Redis, event bus, reconciliation worker health trước live start.

### 3.4 ExecutionEngine đã có gate nghiêm hơn

File: `services/execution-service/execution_service/execution_engine.py`

Điểm tốt:

- Live mode bắt buộc `ExecutionCommand` thay vì raw `OrderRequest`.
- Bắt buộc `brain_cycle_id`, `idempotency_key`, `pre_execution_context`.
- Bắt buộc idempotency reservation verifier.
- Có timeout quanh broker submit.

Điểm cần sửa:

- `gate_ctx["idempotency_exists"]` đang set `False` sau khi verify reservation. Cần phân biệt:
  - reservation exists = OK
  - duplicate consumed/filled = BLOCK
- Phải kiểm tra idempotency status hiện tại, không chỉ kiểm tra reservation có tồn tại.
- Cần emit transition `SUBMIT_TIMEOUT → UNKNOWN` bắt buộc, không chỉ trả OrderResult UNKNOWN.

### 3.5 cTrader provider fail-closed hơn

File: `services/execution-service/execution_service/providers/ctrader.py`

Điểm tốt:

- Live mode yêu cầu account_id.
- Live mode yêu cầu execution adapter available.
- Live mode verify account authorization mismatch.
- Live mode verify candle stream readiness.
- Nếu thiếu trading_core trong live thì raise RuntimeError.

Điểm cần sửa:

- Adapter đang dựa vào engine provider có sẵn method `place_market_order`. Cần contract thật với cTrader Open API/ProtoOA: auth, order submit, execution events, positions, deals, reconnect.
- `comment` chưa được truyền xuống `place_market_order` trong adapter wrapper, trong khi idempotency thường cần đi vào client order/comment.
- `get_order_by_client_id`/`get_executions_by_client_id` chưa nằm trong base provider contract, làm unknown reconciliation thiếu chuẩn.

---

## 4. Vấn đề P0 cần sửa trước khi live

## P0-1 — Daily TP Lock phải thành Runtime Stop Engine

Hiện tại Daily TP mới dừng ở state/event. Cần biến thành engine điều khiển runtime.

### Cần thêm

File mới:

```text
apps/api/app/services/daily_lock_runtime_controller.py
```

Nhiệm vụ:

```python
class DailyLockRuntimeController:
    async def apply_lock_action(bot_id, lock_action):
        if lock_action == "stop_new_orders":
            await registry.pause_new_orders(bot_id)
        elif lock_action == "close_all_and_stop":
            await provider.close_all_positions()
            await registry.stop(bot_id)
        elif lock_action == "reduce_risk_only":
            await registry.set_risk_mode(bot_id, "reduce_only")
```

### Acceptance criteria

- Khi `daily_take_profit_hit`, bot không thể mở lệnh mới trong vòng trading day.
- Nếu policy là `close_all_and_stop`, tất cả vị thế phải được đóng có receipt.
- Ghi `DailyLockEvent` duy nhất cho mỗi bot/ngày/reason.
- Operator reset lock phải ghi audit trail: actor, reason, old state, new state.

---

## P0-2 — RiskContextBuilder phải dùng broker instrument thật

File hiện tại: `services/trading-core/trading_core/risk/risk_context_builder.py`

Vấn đề nghiêm trọng:

```python
projected_margin = margin + notional * 0.01
```

Đây là giả định 1% margin, không đủ cho live. Forex/crypto broker có leverage, contract size, tick value, margin mode, symbol-specific rule khác nhau.

### Cần thêm contract

```python
@dataclass
class InstrumentSpec:
    symbol: str
    contract_size: float
    min_volume: float
    max_volume: float
    volume_step: float
    pip_size: float
    tick_size: float
    tick_value: float
    margin_rate: float
    quote_currency: str
    account_currency: str
```

Provider bắt buộc implement:

```python
async def get_instrument_spec(symbol: str) -> InstrumentSpec
async def estimate_margin(symbol, side, volume, price) -> float
async def get_conversion_rate(from_currency, to_currency) -> float
```

### Acceptance criteria

- Không dùng hardcode `100000` nếu symbol không phải FX standard lot.
- Crypto/perpetual/CFD phải tính margin theo spec riêng.
- Pip value phải convert về account currency.
- Nếu thiếu instrument spec trong live → BLOCK `risk_context_missing_instrument_spec`.

---

## P0-3 — Order ledger phải là source-of-truth thật, không dùng `broker_order_id` chứa idempotency key

File: `apps/api/app/services/order_projection_service.py`

Vấn đề:

```python
# broker_order_id is used as the projection key (idempotency key stored there on creation)
Order.broker_order_id == idempotency_key
```

Đây là thiết kế nguy hiểm vì `broker_order_id` thật và `idempotency_key` là 2 khái niệm khác nhau.

### Cần migration

Thêm vào `orders`:

```sql
ALTER TABLE orders ADD COLUMN idempotency_key VARCHAR(256);
ALTER TABLE orders ADD COLUMN source_attempt_id INTEGER;
CREATE UNIQUE INDEX uq_orders_bot_idempotency ON orders(bot_instance_id, idempotency_key);
```

### Sửa service

- `_find_by_idempotency` phải query `Order.idempotency_key`.
- `broker_order_id` chỉ chứa broker order id thật.
- Khi broker chưa trả order id, order vẫn được insert với `idempotency_key`, status `submitted/unknown`.

### Acceptance criteria

- Có thể trace 1 order bằng idempotency key dù broker_order_id chưa có.
- Khi broker_order_id về sau, update không làm mất projection row.
- Unique constraint chống duplicate order ở DB.

---

## P0-4 — UnknownOrderReconciler phải nối vào worker + DB update + operator queue

File: `services/execution-service/execution_service/unknown_order_reconciler.py`

Hiện tại reconciler tốt về mặt class logic nhưng chưa đủ vòng production.

### Cần hoàn thiện

- Provider base phải có optional/abstract methods:
  - `get_order_by_client_id`
  - `get_executions_by_client_id`
  - `get_position_by_client_id`
- Worker định kỳ quét `broker_order_attempts.current_state = UNKNOWN`.
- Mỗi outcome phải update DB:
  - `filled` → receipt + transition `RECONCILING → FILLED` + orders projection.
  - `rejected` → transition `RECONCILING → REJECTED`.
  - `failed_needs_operator` → incident critical + daily lock.
- Operator UI phải có queue “UNKNOWN ORDERS”.

### Acceptance criteria

- Không có UNKNOWN order nào bị im lặng quá N phút.
- Mỗi UNKNOWN có run log trong `broker_reconciliation_runs`.
- Nếu không resolve được, bot bị lock để không trade tiếp.

---

## P0-5 — LiveStartPreflight phải fail-closed tuyệt đối

File: `apps/api/app/services/live_start_preflight.py`

Cần sửa đoạn fallback:

```python
except Exception:
    state = await daily.get_or_create(bot.id)
```

Live mode không được fallback sang state cũ nếu broker equity sync lỗi.

### Patch rule

```python
except Exception as exc:
    raise LiveStartPreflightError(f"broker_equity_sync_failed:{type(exc).__name__}")
```

### Check thêm

- `alembic_current == alembic_head`
- Redis connected.
- Event publisher connected.
- Reconciliation worker registered.
- Broker account currency known.
- Broker account id match connection config.
- Active risk policy includes `daily_take_profit`, `max_daily_loss_pct`, `max_margin_usage_pct`, `max_account_exposure_pct`.

---

## P0-6 — Legacy backend phải bị khóa khỏi production

Repo vẫn có:

```text
backend/main.py
backend/engine/*
frontend/app.py
```

Và có nhiều dấu hiệu mock/stub legacy:

- `backend/main.py` còn mock data flow.
- `backend/api/trading_brain_routes.py` default broker `stub`.
- `backend/engine/brain_bridge.py` có `brain_stub_unavailable`.

### Cần làm

- Docker production chỉ build `apps/api`, `apps/web`, `services/*`.
- `backend/` chuyển thành `legacy/` hoặc loại khỏi compose production.
- CI fail nếu production Dockerfile import `backend`.
- README ghi rõ `backend/` không phải live runtime.

---

## P0-7 — Broker provider contract chưa đủ cho live multi-broker

File: `services/execution-service/execution_service/providers/base.py`

Hiện base có:

- connect/disconnect
- get_account_info
- get_candles
- place_order
- close_position
- get_open_positions
- get_trade_history

Thiếu cho live thật:

```python
async def get_instrument_spec(symbol)
async def estimate_margin(symbol, side, volume, price)
async def get_order_by_client_id(client_order_id)
async def get_executions_by_client_id(client_order_id)
async def close_all_positions(symbol: str | None = None)
async def get_server_time()
async def get_quote(symbol)
```

Nếu thiếu các method này ở live provider → readiness phải fail.

---

## 5. P1 — Hoàn thiện operator/control plane

### P1-1 — Daily TP UI/API

Cần endpoint:

```text
GET  /v1/workspaces/{workspace_id}/bots/{bot_id}/daily-state
POST /v1/workspaces/{workspace_id}/bots/{bot_id}/daily-lock/reset
POST /v1/workspaces/{workspace_id}/bots/{bot_id}/daily-lock/apply
```

UI hiển thị:

- Starting equity
- Current equity
- Daily PnL
- Daily TP target
- Daily loss limit
- Locked / lock reason
- Reset audit log

### P1-2 — Risk Policy Builder

UI không nên cho nhập JSON raw. Cần form:

- Max daily loss %
- Daily TP mode: fixed / percent equity / capital tier
- Max margin usage %
- Max account exposure %
- Max symbol exposure %
- Max spread pips
- Max slippage pips
- Max consecutive losses
- News/session blackout

### P1-3 — Operator Incident Queue

Mọi event critical phải gom vào queue:

- broker disconnected
- unknown order
- reconciliation mismatch
- daily lock hit
- provider degraded
- equity sync failed
- migration drift

---

## 6. P2 — Production observability & QA

### P2-1 — Metrics cần có

Prometheus metrics:

```text
trading_order_submit_total{broker,status}
trading_order_unknown_total{broker}
trading_order_reconciliation_duration_seconds
trading_daily_lock_total{reason}
trading_pre_execution_block_total{reason}
trading_broker_equity_sync_fail_total
trading_market_data_age_seconds
trading_runtime_state{bot_id,mode,status}
```

### P2-2 — Test suite bắt buộc

Cần thêm tests:

```text
test_live_start_preflight_fails_if_equity_sync_fails.py
test_daily_tp_close_all_and_stop_action.py
test_order_projection_separate_idempotency_and_broker_id.py
test_unknown_order_worker_locks_bot_after_max_retries.py
test_risk_context_requires_instrument_spec_in_live.py
test_provider_contract_requires_client_order_lookup_in_live.py
test_legacy_backend_not_in_production_compose.py
```

### P2-3 — Chaos test

Scenarios:

- Broker submit timeout after order may have been accepted.
- Broker returns ACKED but no fill.
- Broker returns fill but missing order id.
- Redis down during order submit.
- DB commit fail after broker accepted order.
- Provider disconnects during position close.
- Daily TP hit while open positions exist.

---

## 7. Patch order đề xuất

### PATCH P0-A — Live Start Fail-Closed Patch

Mục tiêu:

- Không start live nếu broker equity sync lỗi.
- Check DB migration, Redis, policy, incident, reconciliation worker.

Files:

```text
apps/api/app/services/live_start_preflight.py
apps/api/app/services/live_readiness_guard.py
apps/api/tests/test_live_start_preflight.py
```

### PATCH P0-B — True Order Ledger Patch

Mục tiêu:

- Tách `idempotency_key` khỏi `broker_order_id`.
- Ledger là source-of-truth.
- Projection chỉ là read model.

Files:

```text
apps/api/app/models/__init__.py
apps/api/alembic/versions/0012_order_idempotency_projection.py
apps/api/app/services/order_projection_service.py
apps/api/app/services/safety_ledger.py
apps/api/tests/test_order_projection_service.py
```

### PATCH P0-C — Broker Instrument Risk Patch

Mục tiêu:

- Risk context dùng instrument spec thật.
- Không hardcode 100000/notional*0.01.

Files:

```text
services/execution-service/execution_service/providers/base.py
services/execution-service/execution_service/providers/ctrader.py
services/trading-core/trading_core/risk/instrument_spec.py
services/trading-core/trading_core/risk/risk_context_builder.py
services/trading-core/tests/test_risk_context_builder_live.py
```

### PATCH P0-D — Daily Lock Runtime Controller Patch

Mục tiêu:

- Daily TP hit thực sự pause/stop/close positions.

Files:

```text
apps/api/app/services/daily_profit_lock_engine.py
apps/api/app/services/daily_lock_runtime_controller.py
services/trading-core/trading_core/runtime/bot_runtime.py
apps/api/tests/test_daily_profit_lock_runtime_controller.py
```

### PATCH P0-E — Unknown Order Worker Patch

Mục tiêu:

- UNKNOWN không bao giờ bị im lặng.
- Resolve hoặc lock bot + incident.

Files:

```text
services/execution-service/execution_service/unknown_order_reconciler.py
services/execution-service/execution_service/reconciliation_worker.py
apps/api/app/services/safety_ledger.py
apps/api/app/services/order_projection_service.py
apps/api/tests/test_unknown_order_reconciliation_flow.py
```

### PATCH P0-F — Production No-Legacy Patch

Mục tiêu:

- Production không bao giờ chạy `backend/` legacy.

Files:

```text
infra/docker/docker-compose.prod.yml
.github/scripts/verify_production_no_legacy_stack.sh
.github/workflows/release.yml
README.md
```

---

## 8. Definition of Done cho live trading thật

Chỉ được bật live khi tất cả pass:

```text
[ ] Alembic single head + database upgraded to head
[ ] Production compose không chạy legacy backend
[ ] Live provider không phải paper/stub/degraded/unavailable
[ ] Broker account equity sync pass trước start
[ ] Active risk policy approved by admin
[ ] Daily state fresh <= 60 giây
[ ] Daily TP + Daily loss lock hoạt động
[ ] RiskContext dùng broker instrument spec thật
[ ] OrderRequest raw bị block trong live
[ ] ExecutionCommand có brain_cycle_id + idempotency_key
[ ] Broker receipt có submit_status + fill_status + raw_response
[ ] UNKNOWN order được reconciliation worker xử lý
[ ] Order projection tách idempotency_key khỏi broker_order_id
[ ] Critical incident mở thì bot không start live
[ ] Metrics + logs + operator queue hoạt động
[ ] Chaos test broker timeout pass
```

---

## 9. Kết luận cuối

`forex-main-9` đã đi đúng hướng từ “bot có risk gate” sang “live trading runtime có safety ledger”. Tuy nhiên để thành hệ thống giao dịch tiền thật, cần khóa thêm 6 mảnh sống còn:

1. **Fail-closed live start**
2. **True order ledger**
3. **Broker instrument risk thật**
4. **Daily TP runtime controller**
5. **Unknown order reconciliation worker**
6. **Production no-legacy/no-stub enforcement**

Bước mạnh nhất tiếp theo nên làm là:

# BUILD P0 TRUE LIVE EXECUTION CLOSURE PATCH

Patch này gộp 3 việc trước vì chúng liên quan trực tiếp đến mất tiền thật:

1. Tách `idempotency_key` khỏi `broker_order_id` trong order projection.
2. Chặn live start nếu broker equity/instrument/migration/reconciliation không sẵn sàng.
3. Bắt mọi UNKNOWN order đi qua reconciliation + incident + daily lock.
