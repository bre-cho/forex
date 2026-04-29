# FOREX MAIN 3(1) — DEEP FILE/MODULE COMPLETION AUDIT

> Audit mục tiêu: biến repo thành hệ thống **live trading thật** cho forex/crypto với fail-closed safety, broker source-of-truth, order ledger chuẩn, risk engine chuẩn broker-native, operator control plane, CI không pass giả.

## 0. Kết luận executive

Repo `forex-main-3(1).zip` đã tiến bộ rất rõ so với các bản trước: đã có monorepo, `apps/api`, `apps/web`, `apps/admin`, `services/trading-core`, `services/execution-service`, `ai_trading_brain`, migration safety ledger, daily lock, reconciliation worker, frozen gate context, broker-native risk context, parity contract và nhiều verify scripts.

Tuy nhiên **chưa đạt chuẩn chạy tiền thật không giám sát** vì vẫn còn các lỗi P0 có thể làm live runtime chết hoặc khiến ledger/DB/broker lệch nhau:

1. `services/execution-service/execution_service/providers/ctrader.py` có dấu hiệu lỗi cú pháp nghiêm trọng: duplicate dòng `async def health_check(...)` liên tiếp. File này có thể làm import provider fail toàn bộ live cTrader.
2. `services/trading-core/trading_core/runtime/bot_runtime.py` đã có pipeline live khá tốt nhưng còn dấu hiệu sai idempotency binding: `PreExecutionContext.idempotency_key` và `ExecutionCommand.idempotency_key` đang dùng `signal.signal_id`, trong khi hệ thống đã tạo biến `idempotency_key`. Cần chuẩn hóa ngay để order attempt, reservation, execution receipt và unknown reconciler không lệch khóa.
3. `ExecutionEngine` vẫn tạo gate context giả cho non-live; live path ổn hơn nhưng cần bỏ mọi hardcoded default context khỏi shared execution path để tránh vô tình tái sử dụng trong demo/live.
4. `DailyLockRuntimeController` có fallback close từng position. Với live, fallback này phải có policy rõ: broker không hỗ trợ `close_all_positions` thì fail-closed hoặc chạy close từng position nhưng phải có verify receipt từng lệnh đóng.
5. `ReconciliationWorker` mới repair DB-stale và phát hiện broker ghost position, nhưng chưa tự tạo projection/incident workflow đủ mạnh cho broker ghost position. Đây là rủi ro lớn nhất khi broker có position mà DB không biết.
6. Legacy `backend/` vẫn tồn tại nhiều engine cũ. Dù đã có verify script chống legacy production, repo vẫn cần hard isolation: production image không được import/ship legacy backend.
7. AI brain có vẻ là deterministic/stage engine, nhưng chưa thấy bằng chứng backtest/walk-forward/model-risk governance đủ để tự tin cấp quyền live.

**Production readiness estimate:** 72/100.  
**Live trading readiness:** chỉ phù hợp demo/sandbox/paper. Chưa khuyến nghị chạy tiền thật.

---

## 1. Inventory repo đã đọc

Tổng quan repo:

- Tổng file: 353
- Python: 237
- TSX frontend/admin: 36
- Alembic migrations: 16

Cấu trúc chính:

```text
apps/api/                 FastAPI control plane + DB ledger + auth + routers
apps/web/                 Next.js user dashboard
apps/admin/               Admin operations dashboard
services/trading-core/    Bot runtime, pre-execution gate, risk, engines
services/execution-service/ Broker providers, execution engine, reconciliation
services/analytics-service/ Metrics: drawdown, expectancy, Sharpe, PF
services/signal-service/  Signal feed/scoring/broadcast
services/notification-service/ Alerts via email/telegram/discord/webhook
ai_trading_brain/         Brain contracts/runtime/decision/governance/memory
backend/                  Legacy stack cần cách ly khỏi production
new_files/                Patch staging folder, chưa được wired chính thức
.github/scripts/          Verify scripts chống live stub/fallback/contract drift
.github/workflows/        CI workflows
infra/                    Docker, nginx, monitoring, postgres, redis
```

---

## 2. Module-by-module audit

## 2.1 `apps/api/app/main.py`

Vai trò: entrypoint FastAPI, gắn router, registry, middleware.

Cần kiểm tra/hoàn thiện:

- Đảm bảo lifespan khởi tạo `RuntimeRegistry` đúng một lần.
- Đảm bảo production không mount legacy router hoặc fallback route.
- Thêm startup check bắt buộc:
  - Alembic head đúng.
  - DB connect được.
  - Redis/cache connect được nếu dùng queue/event.
  - `execution_service` import được.
  - `trading_core` import được.
  - Nếu `APP_ENV=production`, `ALLOW_STUB_IN_LIVE=false` bắt buộc.

Patch đề xuất:

```python
if settings.environment == "production":
    assert settings.allow_stub_in_live is False
    assert_runtime_imports()
    assert_no_legacy_backend_imported()
    assert_alembic_head_current()
```

Mức ưu tiên: P0.

---

## 2.2 `apps/api/app/routers/live_trading.py`

Đã có:

- `/timeline`
- `/operations-dashboard`
- `/decision-ledger`
- `/gate-events`
- `/order-state-transitions`
- `/execution-receipts`
- `/account-snapshots`
- `/daily-state`
- `/incidents`
- `/reconciliation-runs`
- `/reconcile-now`
- resolve incident

Điểm tốt:

- Dashboard đã đọc runtime snapshot + ledger + daily state + incidents + reconciliation.
- Có manual reconcile endpoint.

Thiếu production:

1. Chưa thấy endpoint operator hard actions:
   - pause new orders
   - resume new orders
   - kill switch bot
   - close all positions now
   - force broker resync
   - approve/reject policy version
2. Chưa thấy RBAC hard gate cho live action. `current_user` được inject nhưng chưa thấy permission-level enforcement tại từng action.
3. `resolve_incident` chỉ resolve DB incident, chưa bắt buộc kiểm tra mismatch đã hết.

Patch đề xuất:

- Thêm `operator_actions.py` hoặc mở rộng `live_trading.py`:
  - `POST /operator/pause-new-orders`
  - `POST /operator/resume-new-orders`
  - `POST /operator/kill-switch`
  - `POST /operator/close-all-and-stop`
  - `POST /operator/reconcile-and-verify`
- Mọi action ghi `AuditLog` + `TradingIncident` nếu failure.
- `resolve_incident` chỉ cho resolve nếu `reconcile_now.status == ok` hoặc có override reason.

Mức ưu tiên: P0/P1.

---

## 2.3 `apps/api/app/services/live_start_preflight.py`

Đã có:

- Broker readiness guard.
- Active policy approval required.
- Required policy keys: `daily_take_profit`, `max_daily_loss_pct`, `max_margin_usage_pct`, `max_account_exposure_pct`.
- Sync broker equity fail-closed.
- Daily state freshness.
- Unknown orders check.
- Critical incident check.

Điểm tốt:

- Đây là đúng hướng live trading production.

Thiếu/harden:

1. Required policy keys vẫn chưa đủ. Cần thêm:
   - `max_risk_amount_per_trade`
   - `max_lot_per_trade`
   - `min_lot_per_trade`
   - `max_spread_pips`
   - `max_slippage_pips`
   - `max_data_age_seconds`
   - `require_stop_loss_live`
   - `max_daily_trades`
   - `new_orders_paused`
2. Preflight phải validate provider live contract:
   - `supports_client_order_id == True`
   - `get_order_by_client_id` real method
   - `get_executions_by_client_id` real method
   - `estimate_margin` real method
   - `get_instrument_spec` real method
   - `get_quote` real method
3. Preflight cần test dry-run quote + margin + instrument spec cho symbol của bot trước start.

Patch đề xuất:

```python
await assert_live_provider_contract(provider, symbol=bot.symbol)
await assert_policy_schema_complete(active_policy.policy_snapshot)
await assert_no_unresolved_unknown_orders(bot.id)
await assert_reconciliation_first_pass_ok(bot.id, provider)
```

Mức ưu tiên: P0.

---

## 2.4 `apps/api/app/services/live_readiness_guard.py`

Đã có:

- Chặn provider missing/not connected.
- Chặn mode `stub`, `paper`, `unavailable`, `degraded`.
- Health check.
- Account equity > 0.

Thiếu:

- Chưa kiểm tra live-required methods override thật hay vẫn inherited base method.
- Chưa kiểm tra server time drift.
- Chưa kiểm tra quote freshness/spread.

Patch đề xuất:

```python
required = [
  "get_instrument_spec", "estimate_margin",
  "get_order_by_client_id", "get_executions_by_client_id",
  "close_all_positions", "get_quote"
]
for name in required:
   assert callable(getattr(provider, name, None))
   assert not is_base_notimplemented(provider, name)
```

Mức ưu tiên: P0.

---

## 2.5 `apps/api/app/services/bot_service.py`

Vai trò: tạo runtime từ DB config, credential, provider, hooks, ledger callbacks.

Đã có:

- Load `BotInstanceConfig`, `BrokerConnection`.
- Decrypt credentials.
- RuntimeFactory provider.
- Live provider usable assertion.
- Hook injection: signal/order/trade/snapshot/event/reservation/daily/reconciliation.
- Stub fallback bị chặn nếu live import fail.
- Readiness mode derivation.
- Live guard stop runtime nếu provider/runtime xấu.

Điểm cần sửa:

1. `_register_stub()` vẫn tồn tại. Không xóa nhưng phải đảm bảo production không bao giờ gọi cho live. Hiện có chặn live import error, tốt. Cần CI verify branch này không chạy trong production image.
2. `_derive_provider_mode()` bị duplicate line `provider_health_status = "disconnected"` trong đoạn output. Cần dọn.
3. Hook event phải phân biệt rõ:
   - `order_submitted`
   - `order_unknown`
   - `order_rejected`
   - `order_filled`
   - `trade_opened`
   - `unknown_resolved`
4. Cần đảm bảo `on_order` gọi `OrderLedgerService` chứ không chỉ record broker event.

Patch đề xuất:

- Thêm `create_runtime_for_bot` P0 checks:
  - `bot.mode == live` phải có `broker_connection_id`.
  - credential đủ keys theo broker.
  - broker account id match.
  - active policy approved.
  - live preflight chạy trước `registry.start`.
- Thêm audit log cho mọi runtime lifecycle action.

Mức ưu tiên: P0.

---

## 2.6 `apps/api/app/services/order_ledger_service.py`

Vai trò: orchestration order lifecycle persistence.

Đã có:

- Safety ledger là source-of-truth: `broker_order_attempts`, `order_state_transitions`, `broker_execution_receipts`.
- Projection sang `orders`.
- Unknown order enqueue.

Thiếu/harden:

1. Cần đảm bảo mọi path gọi ledger:
   - `order_submitted` => attempt status `SUBMITTED`
   - `order_unknown` => attempt status `UNKNOWN` + enqueue reconciliation queue
   - `order_rejected` => receipt + transition `REJECTED`
   - `order_filled` => receipt + transition `FILLED` + projection order open
2. Cần reject nếu `idempotency_key != client_order_id` hoặc thiếu `brain_cycle_id` trong live.
3. Cần unique constraint đầy đủ:
   - `(bot_instance_id, idempotency_key)`
   - `(bot_instance_id, broker_order_id)` nullable-safe
   - `(bot_instance_id, brain_cycle_id, signal_id)`
4. Cần transaction atomic. Hiện nhiều service gọi `commit` riêng; live order lifecycle nên có một transaction unit hoặc outbox pattern.

Patch đề xuất:

```python
async with db.begin():
    attempt = await ledger.create_or_get_order_attempt(...)
    await ledger.record_state_transition(...)
    if receipt:
        await ledger.record_execution_receipt(...)
    await projection.upsert(...)
    await outbox.publish_after_commit(...)
```

Mức ưu tiên: P0.

---

## 2.7 `apps/api/app/services/safety_ledger.py`

Đã có:

- Brain cycle ledger.
- Idempotency reservation.
- Gate events.
- Broker order event.
- Order attempt.
- Status update.
- Execution receipt.
- Daily state/account snapshot/reconciliation/incidents.

Điểm tốt:

- Đây là nền tảng đúng cho production auditability.

Cần sửa:

1. `record_gate_event()` đang không lưu `gate_context_hash` dù payload có hash. Migration có vẻ đã thêm hash ở attempt nhưng gate event cũng cần hash để audit replay.
2. `record_broker_order_event()` dùng `broker_order_id` rỗng được. Với `order_unknown` thì được, nhưng phải bắt buộc `idempotency_key`.
3. `reserve_idempotency()` có thứ tự param `(bot_instance_id, signal_id, idempotency_key)` trong service. Trong hooks cần kiểm tra không bị gọi đảo thứ tự.
4. Cần `raw_response_hash` được tính server-side, không tin client/runtime payload.

Mức ưu tiên: P0/P1.

---

## 2.8 `apps/api/app/services/daily_profit_lock_engine.py` + `daily_trading_state.py`

Đã có:

- Daily state service.
- Recompute from broker equity.
- Daily lock policy.
- Lock event.

Cần hoàn thiện:

1. Daily TP/loss phải là runtime-enforced, không chỉ DB state.
2. Khi lock hit:
   - mark state locked
   - pause new orders ngay trong `RuntimeRegistry`
   - emit incident/audit
   - nếu `close_all_and_stop`, verify broker positions còn 0
3. Reset daily state theo trading timezone/sàn, không dùng local server tuỳ tiện.
4. Daily TP dynamic theo vốn:
   - vốn nhỏ target thấp
   - vốn lớn target theo pct hoặc tier
   - hard max absolute theo account.

Patch đề xuất:

```json
"daily_take_profit": {
  "enabled": true,
  "mode": "tiered_pct",
  "tiers": [
    {"min_equity": 0, "max_equity": 500, "target_pct": 1.0},
    {"min_equity": 500, "max_equity": 2000, "target_pct": 0.8},
    {"min_equity": 2000, "target_pct": 0.5}
  ],
  "lock_action": "stop_new_orders"
}
```

Mức ưu tiên: P0.

---

## 2.9 `apps/api/app/services/daily_lock_runtime_controller.py`

Đã có:

- `stop_new_orders`
- `close_all_and_stop`
- `reduce_risk_only`
- Verify remaining positions after close.

Cần sửa:

1. Live mode không nên silent fallback close từng position nếu `close_all_positions` không có receipt. Fallback phải generate per-position closure receipts.
2. Nếu close incomplete, phải set kill switch + incident critical.
3. `apply_lock_action()` hiện catch exception và trả `outcome=error`; caller phải bắt buộc block runtime nếu error.

Patch đề xuất:

- Thêm `strict_live=True`.
- Nếu provider thiếu `close_all_positions` trong live: `raise RuntimeError("live_close_all_positions_contract_missing")`.
- Ghi `DailyLockActionResult` DB table hoặc AuditLog.

Mức ưu tiên: P0.

---

## 2.10 `services/trading-core/trading_core/runtime/bot_runtime.py`

Đây là module quan trọng nhất.

Đã có:

- Runtime loop.
- Market data quality check.
- Brain cycle.
- Live path không dùng legacy queue.
- Stop loss required in live.
- Position sizing enforcement.
- Daily state refresh.
- Daily profit lock evaluation.
- Pre-execution gate.
- Idempotency reservation.
- ExecutionCommand.
- Receipt-grade live requirement.
- Unknown order path.
- Reconciliation worker start.

Điểm tốt:

- Kiến trúc đã đúng hướng production.

P0 issues:

1. **Idempotency binding có nguy cơ sai**  
   Trong đoạn tạo `PreExecutionContext` và `ExecutionCommand`, field đang dùng `str(signal.signal_id)` cho `idempotency_key`. Trong khi trước đó hệ thống đã tạo biến `idempotency_key`. Nếu hai giá trị không giống nhau, ledger/reservation/reconciliation sẽ lệch.

   Patch bắt buộc:

   ```python
   pre_ctx = PreExecutionContext(
       ...
       idempotency_key=str(idempotency_key),
       ...
   )
   command = ExecutionCommand(
       ...
       idempotency_key=str(idempotency_key),
       ...
   )
   ```

2. **Gate hash phải freeze sau reservation**  
   Gate context được hash trước reservation. Nhưng context có `idempotency_exists=False`; sau reservation, đúng ra frozen context nên lưu `idempotency_reserved=True` hoặc reservation id. Nếu không, audit replay không phản ánh đúng state tại submit.

3. **Order payload status mapping quá đơn giản**  
   `status = "filled" if result.success else "rejected"`. Trong live, success false nhưng submit/fill unknown phải `unknown`, đã xử lý sau nhưng payload ban đầu vẫn có thể ghi sai nếu hook gọi trước. Cần build status bằng state machine.

4. **`order_unknown` phải được ghi ledger + enqueue queue trước khi return**  
   Hiện emit event phụ thuộc hook. Cần bắt buộc hook failure = fail loud/incident.

5. **Market data `data_age_seconds` từ dataframe index có thể sai nếu index không timezone-aware**. Live phải dùng broker server time hoặc candle timestamp UTC.

6. **`state.open_trades` dùng broker positions count nhưng DB open trades cũng cần sync**. Trước khi gate phải dùng broker + DB reconciliation snapshot.

Patch P0:

- Extract live execution into `LiveExecutionPipeline` riêng:
  - build signal
  - brain cycle
  - risk sizing
  - daily state sync
  - gate context freeze
  - reserve idempotency
  - submit broker
  - classify receipt
  - persist ledger transaction
  - reconcile if unknown
- Không để `_execute_signal()` dài > 200 dòng.

Mức ưu tiên: P0.

---

## 2.11 `services/trading-core/trading_core/runtime/pre_execution_gate.py`

Đã có gate tốt:

- kill switch
- daily lock
- new orders paused
- policy approved
- provider mode/live checks
- broker connected
- market data stale
- daily loss
- portfolio loss
- daily TP
- consecutive losses
- margin usage
- max risk amount
- lot min/max
- account/symbol/correlated exposure
- slippage/spread
- open positions
- idempotency duplicate
- confidence/RR

Cần hoàn thiện:

1. Live phải require stop loss explicitly trong gate, không chỉ runtime.
2. `starting_equity` đang dùng trong daily TP target nhưng `GateContext` canonical không có field `starting_equity`. Hash canonical có thể bỏ mất key này. Cần thêm vào `GateContext`.
3. `slippage_pips` được evaluate nhưng không có trong `GateContext`; canonical hash cũng bỏ mất. Cần thêm.
4. `symbol`, `side` được đưa vào gate_ctx nhưng `GateContext.from_dict` bỏ qua; context hash sẽ không bind symbol/side. Đây là lỗi audit nghiêm trọng.

Patch P0:

Thêm vào `GateContext`:

```python
symbol: str = ""
side: str = ""
starting_equity: float = 0.0
slippage_pips: float = 0.0
account_id: str = ""
broker_name: str = ""
policy_version: str = ""
```

Nếu không, frozen context hash không đủ khóa lệnh với symbol/side/account/policy.

Mức ưu tiên: P0.

---

## 2.12 `services/trading-core/trading_core/runtime/frozen_context_contract.py`

Đã có validate binding giữa request/context/provider.

Cần hoàn thiện:

- Validate `context.idempotency_key == command.idempotency_key`.
- Validate `context.gate_context["symbol"] == request.symbol`.
- Validate `context.gate_context["side"] == request.side`.
- Validate `context_hash == hash_gate_context(context.gate_context)`.
- Validate `policy_version_approved == True`.
- Validate `daily_locked == False`.

Mức ưu tiên: P0.

---

## 2.13 `services/trading-core/trading_core/risk/risk_context_builder.py`

Đã có:

- Live mode require instrument spec.
- Live mode require broker margin estimate.
- Pip value check.
- Exposure / margin / max loss calculation.

Cần sửa:

1. Forex pip value không đơn giản nếu account currency khác quote currency. `pip_value_per_lot_usd` dễ sai với JPY/cross/crypto.
2. Exposure tính `notional/equity` có thể cực cao với forex leverage, nhưng vẫn hữu ích. Cần tách:
   - notional exposure
   - margin exposure
   - risk-at-SL exposure
3. Correlated USD exposure hiện proxy `if "USD" in symbol`. Cần correlation bucket thật.
4. Open positions schema provider khác nhau: `volume`, `qty`, `lots`, `symbol`, `open_price`. Cần normalizer.

Patch P1:

- Thêm `PositionNormalizer`.
- Thêm `CurrencyConversionService`.
- Thêm `CorrelationExposureEngine`.
- Thêm `RiskContextV2` có:
  - `notional_exposure_pct`
  - `margin_usage_pct`
  - `risk_at_sl_pct`
  - `symbol_net_exposure_pct`
  - `correlation_bucket_exposure_pct`

Mức ưu tiên: P0/P1.

---

## 2.14 `services/trading-core/trading_core/risk/position_sizing.py`

Vai trò: tính lot theo equity, risk pct, SL, pip value, min/max/step.

Cần kiểm tra/harden:

- Reject nếu SL = 0 trong live.
- Reject nếu pip_value <= 0.
- Round lot theo broker lot step.
- Verify lot không vượt broker min/max.
- Verify max loss amount <= policy.
- Add tests cho JPY, XAUUSD, BTCUSD, US30 nếu hỗ trợ.

Mức ưu tiên: P0/P1.

---

## 2.15 `services/execution-service/execution_service/execution_engine.py`

Đã có:

- Provider lifecycle.
- Live requires ExecutionCommand.
- Live requires supports_client_order_id.
- Live requires brain_cycle_id/idempotency_key/pre_execution_context.
- Verify idempotency reservation.
- Verify frozen gate context hash.
- Validate frozen context binding.
- Submit timeout -> UNKNOWN.
- Broker exception -> UNKNOWN.

Điểm tốt:

- Đây là một trong các module production-ready nhất.

Cần sửa:

1. Non-live fake gate_ctx không nên nằm cùng function với live execution. Tách `_build_non_live_gate_context()` để tránh dev copy sang live.
2. Timeout handling dùng `except TimeoutError`; trong asyncio thường nên bắt `asyncio.TimeoutError` rõ ràng.
3. Sau provider result, cần normalize/validate receipt contract ngay tại engine, không để runtime tự suy luận.
4. Cần hash raw_response nếu provider không cung cấp.

Patch đề xuất:

```python
result = normalize_order_result(result)
result.raw_response_hash = result.raw_response_hash or hash_raw_response(result.raw_response)
validate_execution_receipt_or_unknown(result)
```

Mức ưu tiên: P0/P1.

---

## 2.16 `services/execution-service/execution_service/providers/base.py`

Đã có contract tốt:

- `OrderRequest`
- `OrderResult`
- `ExecutionReceipt`
- `AccountInfo`
- `PreExecutionContext`
- `ExecutionCommand`
- `BrokerProvider` abstract methods
- Live optional methods throw NotImplemented.

Cần sửa:

1. `OrderRequest` thiếu `client_order_id/idempotency_key`. Đang dùng `comment` để truyền idempotency. Production nên có field riêng.
2. `AccountInfo` thiếu `account_id`, `server_time`, `raw_response`, `leverage`.
3. `OrderResult.server_time` là float nhưng cần datetime/epoch rõ chuẩn.
4. `ExecutionReceipt` thiếu `server_time`, `account_id`, `symbol`, `side`, `order_type`.

Patch P0:

```python
@dataclass
class OrderRequest:
    ...
    client_order_id: str = ""
    idempotency_key: str = ""
```

Và provider bắt buộc truyền client order id vào broker nếu broker hỗ trợ.

Mức ưu tiên: P0.

---

## 2.17 `services/execution-service/execution_service/providers/ctrader.py`

Đã đọc và phát hiện lỗi nghiêm trọng:

```python
async def health_check(self) -> Dict[str, Any]:
async def health_check(self) -> Dict[str, Any]:
```

Đây là lỗi P0. File có thể không import được. Nếu cTrader là live provider chính, hệ thống live sẽ chết ngay ở runtime/import.

Ngoài ra:

- `place_order()` chưa truyền `client_order_id` field riêng, chỉ truyền `comment=request.comment`.
- `get_quote()` có fallback derive bid/ask từ candle close với spread 0. Trong live, fallback quote từ candle phải bị cấm vì sẽ làm spread/slippage guard pass giả.
- `get_server_time()` fallback `time.time()` trong live cũng không nên chấp nhận nếu broker server time required.
- `get_order_by_client_id()` fallback search history 500 record. Tạm chấp nhận demo, nhưng live nên có broker-native lookup hoặc documented limitation.

Patch P0:

```python
# remove duplicate health_check line

if self.live and not quote_from_broker:
    raise RuntimeError("ctrader_live_quote_unavailable")

if self.live and request.client_order_id == "":
    return rejected/unknown: missing_client_order_id
```

Mức ưu tiên: P0.

---

## 2.18 `services/execution-service/execution_service/providers/mt5.py`

Quan sát từ grep:

- Có fallback stub/paper khi SDK unavailable.
- Live phải fail closed nếu MT5 SDK unavailable.

Cần kiểm tra/harden:

- `supports_client_order_id` có thật không? MT5 có comment/magic, nhưng lookup theo comment không luôn đáng tin. Cần định nghĩa limitation.
- `estimate_margin` phải dùng `order_calc_margin`.
- `get_instrument_spec` phải dùng `symbol_info`.
- `get_quote` phải dùng `symbol_info_tick`.
- `place_order` phải trả retcode, deal, order, price, volume, comment.

Mức ưu tiên: P0/P1.

---

## 2.19 `services/execution-service/execution_service/providers/bybit.py`

Quan sát từ grep:

- Pybit unavailable => stub/paper warning.
- Crypto provider cần testnet/live separation.

Cần hoàn thiện:

- Live requires `testnet=False` plus explicit `CONFIRM_BYBIT_LIVE=true`.
- Use `orderLinkId` for idempotency.
- Unknown reconciler lookup by `orderLinkId`.
- Instrument spec via exchange info.
- Margin via exchange risk API.
- Leverage/mode isolated per symbol.

Mức ưu tiên: P1 nếu crypto live nằm trong scope gần.

---

## 2.20 `services/execution-service/execution_service/reconciliation_worker.py`

Đã có:

- Broker open positions vs DB open trades.
- Auto close stale DB trade.
- Detect broker ghost position.
- Persistent mismatch incident.
- Account snapshot.
- Unknown order hooks.

Cần sửa P0:

1. `broker_ids` đang lấy `p.get("id") or p.get("broker_order_id")`. Nhiều broker dùng `positionId`, `ticket`, `order`, `deal`. Cần provider-normalized position id.
2. Ghost broker position không chỉ informational. Live phải:
   - freeze new orders
   - create critical incident
   - optionally import into DB as unmanaged position
   - require operator decision: close/import/ignore with reason
3. Worker auto-close DB stale trade cần ghi close reason, close timestamp, PnL nếu có.
4. Unknown order resolver phải chạy trong worker loop đầy đủ, không chỉ manual/after unknown.

Patch P0:

- `PositionIdentityNormalizer`.
- `GhostPositionPolicy`:
  - `freeze_new_orders_on_ghost=True`
  - `escalation_action=kill_switch` after N rounds.
- `UnmanagedBrokerPosition` table/projection.

Mức ưu tiên: P0.

---

## 2.21 `services/execution-service/execution_service/unknown_order_reconciler.py`

Đã có:

- Direct lookup by client id.
- Execution/deal lookup.
- Classify filled/rejected/still_unknown.
- Max retry => failed_needs_operator.
- Live provider unsupported => error.

Cần hoàn thiện:

- Classifier phải normalize broker-specific statuses.
- Partial fill handling: hiện pending/partial có thể still_unknown. Production phải state `PARTIAL_FILLED`.
- Nếu order not found sau enough time, có thể `REJECTED_OR_NOT_ACCEPTED`, nhưng không được tự coi rejected nếu network timeout.
- Must update `broker_order_attempts`, `execution_receipts`, `orders_projection`, `incidents` in one hook.

Mức ưu tiên: P0/P1.

---

## 2.22 `services/execution-service/execution_service/parity_contract.py`

Vai trò: validate contract parity giữa paper/demo/live.

Cần mở rộng:

- Live required fields:
  - `idempotency_key`
  - `brain_cycle_id`
  - `gate_context_hash`
  - `client_order_id`
  - `broker_order_id` or unknown path
  - `submit_status`
  - `fill_status`
  - `raw_response_hash`
- Add mode-specific tests.

Mức ưu tiên: P0/P1.

---

## 2.23 `ai_trading_brain/*`

Files:

- `brain_contracts.py`
- `brain_runtime.py`
- `decision_engine.py`
- `engine_registry.py`
- `evolution_engine.py`
- `governance.py`
- `memory_engine.py`
- `unified_trade_pipeline.py`

Vai trò: brain quyết định trade.

Cần kiểm tra/harden:

1. Live brain không được dùng LLM nondeterministic trực tiếp để đặt lệnh.
2. Brain output phải có:
   - `cycle_id`
   - `action`: ALLOW/SKIP/BLOCK/PAUSE
   - `reason`
   - `final_score`
   - `execution_intent`
   - `policy_snapshot`
   - `stage_decisions`
3. Cần model-risk governance:
   - backtest metrics
   - walk-forward result
   - max drawdown
   - min sample size
   - allowed symbol/timeframe
   - expiration of strategy approval
4. Evolution engine không được tự động mutate live policy nếu chưa approval.

Patch P0/P1:

- `BrainDecisionContractV2`.
- `StrategyApprovalRegistry`.
- `LiveBrainKillSwitch` nếu brain exception.

Mức ưu tiên: P0/P1.

---

## 2.24 `services/trading-core/trading_core/data/market_data_quality.py`

Đã được BotRuntime gọi.

Cần hoàn thiện:

- Detect missing candles.
- Detect duplicate timestamp.
- Detect stale last candle.
- Detect zero volume nếu exchange cần volume.
- Detect abnormal spread/price jump.
- Live must use broker timestamp/server time.

Mức ưu tiên: P1.

---

## 2.25 `apps/web/*` và `apps/admin/*`

Đã có:

- Live control center.
- Live orders.
- Runtime control.
- Trading brain.
- Broker connections.
- Admin broker health/operations dashboard/runtime/users/workspaces.
- Components: DailyLockPanel, ExecutionReceiptDrawer, LiveReadinessPanel, ReconciliationTimeline, UnknownOrdersPanel.

Cần hoàn thiện:

1. UI phải hiển thị trạng thái chặn live rõ:
   - broker mode
   - runtime mode
   - policy approved
   - daily lock
   - unknown orders
   - critical incidents
   - reconciliation status
2. UI cần action buttons với confirm modal:
   - pause new orders
   - close all and stop
   - reconcile now
   - resolve incident
   - approve policy
3. UI phải Việt hóa 100% nếu theo quy tắc người dùng.
4. Frontend API contract test phải cover live operator routes.

Mức ưu tiên: P1.

---

## 2.26 `backend/` legacy

Legacy backend còn rất nhiều engine:

- adaptive_controller
- auto_pilot
- autonomous_enterprise_engine
- ctrader_provider
- data_provider
- decision_engine
- risk_manager
- trade_manager
- synthetic_engine
- etc.

Rủi ro:

- Dev có thể import nhầm legacy engine vào production.
- Logic cũ có thể không đi qua ledger/gate/reconciliation.

Đã có verify scripts:

- `verify_production_no_legacy_stack.sh`
- `verify_live_import_boundary.py`
- `check_legacy_backend_drift.sh`

Patch đề xuất:

- Move legacy to `legacy_backend/` hoặc `archive/`.
- Production Dockerfile không copy `backend/`.
- CI fail nếu `apps/api` hoặc `services/*` import `backend.`.
- Thêm `backend/README_DEPRECATED.md`.

Mức ưu tiên: P0.

---

## 3. P0 Patch Plan — thứ tự bắt buộc

## P0.1 — Syntax/import truth patch

Mục tiêu: repo phải import/compile trước khi bàn live.

Fix:

- `services/execution-service/execution_service/providers/ctrader.py`
  - Xóa duplicate `async def health_check`.
  - Add test import provider.
- Run CI:
  - `python -m compileall apps services ai_trading_brain`
  - `pytest services/execution-service/tests -q`
  - `pytest services/trading-core/tests -q`

Acceptance:

- Không file Python syntax error.
- cTrader provider import được.
- Live readiness tests pass.

---

## P0.2 — Idempotency binding hardlock

Mục tiêu: một order chỉ có một khóa xuyên suốt.

Fix:

- `BotRuntime._execute_signal()`:
  - `idempotency_key` biến chuẩn phải đi vào:
    - DB reservation
    - gate event
    - PreExecutionContext
    - ExecutionCommand
    - OrderRequest.client_order_id/comment
    - order_payload
    - unknown queue
    - receipt
- `OrderRequest` thêm `client_order_id`, `idempotency_key`.
- Providers dùng field riêng thay vì comment tự do.

Acceptance:

- Test assert cùng một key xuất hiện trong reservation, attempt, receipt, unknown queue, broker comment/orderLinkId.

---

## P0.3 — Frozen gate context v2

Mục tiêu: hash gate context bind đủ symbol/side/account/policy/order.

Fix:

- Add fields vào `GateContext`:
  - symbol
  - side
  - account_id
  - broker_name
  - starting_equity
  - slippage_pips
  - policy_version
  - idempotency_key
- Update `hash_gate_context` tests.
- Update `validate_frozen_context_bindings`.

Acceptance:

- Nếu đổi symbol/side/volume/policy/account thì hash đổi và execution bị block.

---

## P0.4 — Live provider contract hardening

Mục tiêu: broker thật phải có đủ method thật.

Fix:

- `LiveReadinessGuard.assert_live_provider_contract()`.
- cTrader:
  - no candle quote fallback in live.
  - no local server time fallback if broker server time required.
  - require client order id.
- MT5/Bybit tương tự.

Acceptance:

- Live bot không start nếu provider thiếu instrument spec, margin estimate, quote, client id lookup.

---

## P0.5 — Order ledger atomic source-of-truth

Mục tiêu: event/order/receipt/projection không lệch.

Fix:

- `OrderLedgerService.record_lifecycle_event(event_type, payload)` transaction atomic.
- `order_unknown` enqueue reconciliation in same transaction.
- `raw_response_hash` server-side.
- State machine enum strict.

Acceptance:

- Simulate broker timeout -> attempt UNKNOWN + queue row + incident if unresolved.
- Simulate filled -> receipt + projection open trade.
- Simulate rejected -> receipt + projection rejected.

---

## P0.6 — Reconciliation ghost position policy

Mục tiêu: broker position không có trong DB không được chỉ “informational”.

Fix:

- Position identity normalizer.
- If broker ghost position:
  - pause new orders immediately
  - create critical incident
  - show in `UnknownOrdersPanel`/`ReconciliationTimeline`
  - require operator action

Acceptance:

- Inject ghost position test => bot new orders paused + incident critical.

---

## P0.7 — Daily TP/loss runtime lock hardlock

Mục tiêu: chạm TP/loss là dừng thật, không chỉ ghi DB.

Fix:

- Daily lock event calls `DailyLockRuntimeController`.
- In live, close action must verify positions closed with receipts.
- If controller returns error => kill switch + critical incident.

Acceptance:

- Test daily profit hit => no new order accepted.
- Test close_all incomplete => critical incident + kill switch.

---

## P0.8 — Production legacy isolation

Mục tiêu: live production không thể chạy code cũ.

Fix:

- Docker prod không copy `backend/`.
- CI grep import boundary.
- Add package denylist.

Acceptance:

- `verify_production_no_legacy_stack.sh` pass.
- Any import `backend.` from new stack fails CI.

---

## 4. P1 Patch Plan — nâng lên production-grade

1. Broker adapters full contract:
   - cTrader, MT5, Bybit each has real contract tests.
2. RiskContextV2:
   - pip conversion by account currency.
   - correlation buckets.
   - risk-at-SL percent.
3. Operator Control Center:
   - live action buttons + RBAC + audit.
4. Backtest/walk-forward approval gate:
   - strategy cannot go live without evidence.
5. Monitoring:
   - Prometheus metrics: unknown orders, rejected, slippage, reconciliation mismatch, broker latency.
6. Alerting:
   - Telegram/email/webhook on critical incidents.
7. E2E live sandbox smoke:
   - submit minimal order to demo/testnet and reconcile.

---

## 5. P2 Patch Plan — scale/automation

1. Multi-broker failover read-only quotes.
2. Portfolio-level exposure across bots/workspaces.
3. Strategy tournament but only paper/demo.
4. Auto-decrease risk after drawdown.
5. Capital allocation engine.
6. Trade journal + post-trade attribution.
7. Model governance dashboard.

---

## 6. Final recommendation

Bản `forex-main-3(1)` đã gần một hệ thống live trading nghiêm túc hơn nhiều bản trước. Nhưng trước khi chạy tiền thật, dev nên làm ngay theo thứ tự:

1. **P0.1 Syntax/import truth patch** — sửa cTrader duplicate `health_check` và compile toàn repo.
2. **P0.2 Idempotency binding hardlock** — không để `signal_id` và `idempotency_key` lệch.
3. **P0.3 Frozen gate context v2** — hash phải bind symbol/side/account/policy/slippage/starting equity.
4. **P0.4 Live provider contract hardening** — broker thật phải có quote/margin/spec/client id lookup thật.
5. **P0.5 Order ledger atomic source-of-truth** — mọi order path đi qua ledger transaction.
6. **P0.6 Reconciliation ghost policy** — broker ghost position = pause + incident.
7. **P0.7 Daily TP/loss runtime lock** — lock phải tác động runtime thật.
8. **P0.8 Legacy isolation** — production image không có legacy backend.

Sau 8 patch này, repo mới đạt ngưỡng chạy **demo live broker / micro-lot supervised**. Muốn chạy tiền thật không giám sát cần thêm P1 monitoring, alerting, broker contract tests và strategy approval/backtest governance.
