# FOREX-MAIN-4(1) — FULL FILE/MODULE/ENGINE COMPLETION AUDIT

**Mục tiêu audit:** nghiên cứu bản `forex-main-4(1).zip` để đánh giá khả năng nâng cấp thành hệ thống **live trading thật** cho Forex/Crypto, theo tiêu chuẩn production: fail-closed, broker-native risk, order ledger source-of-truth, reconciliation, daily TP/loss lock, operator control, CI chống mock/legacy.

> Cảnh báo: Không có hệ thống bot nào có thể đảm bảo lợi nhuận 500% từ 500$. Báo cáo này chỉ tập trung vào chất lượng kỹ thuật, an toàn vận hành, kiểm soát rủi ro và điều kiện cần trước khi chạy tiền thật.

---

## 1. Kết luận executive

Bản `forex-main-4(1)` **tiến bộ rõ so với các bản trước**. Repo đã có nhiều module đúng hướng production:

- Monorepo rõ: `apps/api`, `apps/web`, `apps/admin`, `services/trading-core`, `services/execution-service`, `ai_trading_brain`, `infra`, `docs`.
- Đã có live safety migrations đến `0016_execution_receipt_contract.py`.
- Đã có `ExecutionCommand`, `PreExecutionContext`, `OrderResult` dạng receipt-grade.
- Đã có `PreExecutionGate`, `FrozenContextContract`, `RiskContextBuilder`, `BrokerNativeRiskContext`.
- Đã có `OrderLedgerService`, `SafetyLedgerService`, `OrderProjectionService`, `ReconciliationQueueService`.
- Đã có `UnknownOrderReconciler`, `ReconciliationWorker`, live start preflight, daily lock runtime controller.
- Frontend đã có `live-control-center`, `live-orders`, `runtime-control`, `DailyLockPanel`, `LiveReadinessPanel`, `UnknownOrdersPanel`.
- CI đã có nhiều script verify live safety: no stub, no fallback, live import boundary, broker gate wiring, production no legacy stack.

Nhưng bản này **vẫn chưa đạt chuẩn chạy tiền thật không giám sát**. Lý do chính:

1. `backend/` legacy vẫn còn đủ engine và API cũ, dễ drift và bị import nhầm.
2. `bot_runtime.py` quá lớn, gom nhiều trách nhiệm sống còn vào một file ~77KB; khó test, khó chứng minh bất biến.
3. Live execution còn phụ thuộc nhiều hook callback; nếu một hook thiếu hoặc event persistence fail ở sai điểm, trạng thái order có thể lệch.
4. `provider_name` trong `ExecutionEngine` đang truyền `bot_instance_id`, trong khi frozen context kiểm `broker_name == provider_name`; điều này có nguy cơ mismatch giữa broker identity và runtime identity.
5. cTrader provider vẫn là wrapper quanh `trading_core.engines.ctrader_provider.CTraderDataProvider`, trong khi provider core hiện còn fallback/paper behavior; cần adapter live thật với Open API contract rõ hơn.
6. `RiskContextBuilder` vẫn tính exposure bằng notional đơn giản; live cần broker-native exposure/margin/currency conversion/correlation chính xác hơn.
7. Daily lock đã có nhưng chưa thành một policy lifecycle hoàn toàn atomic: lock event → runtime action → verify broker positions → persist outcome → block restart.
8. Unknown order queue đã có nhưng cần worker nền/lease/retry/dead-letter rõ ràng hơn để không chỉ chạy khi runtime kích hoạt.
9. Frontend operator panel có nhiều màn hình tốt nhưng readiness check UI chưa phản ánh đầy đủ preflight thật: policy approval, unresolved queue, broker receipt integrity, account sync age.

**Verdict:**

- Paper/demo: có thể tiếp tục test E2E.
- Live nhỏ có giám sát: chỉ sau khi sửa P0 bên dưới.
- Live production không giám sát: chưa đạt.

---

## 2. Inventory cấp cao

Repo có khoảng **353 file**. Các vùng chính:

```text
ai_trading_brain/                       AI brain / governance / memory
apps/api/                               FastAPI API, DB models, routers, services, migrations
apps/web/                               Next.js app cho user/operator
apps/admin/                             Admin dashboard
services/trading-core/                  Runtime, signal, risk, gate, engines
services/execution-service/             Broker providers, order routing, reconciliation
services/signal-service/                Signal feed / scoring
services/analytics-service/             Drawdown, equity, expectancy, PF, sharpe
services/notification-service/          Email/Telegram/Discord/Webhook
services/billing-service/               Plan rules, entitlements, Stripe client
backend/                                Legacy stack — phải cách ly khỏi production
frontend/                               Legacy frontend — phải cách ly khỏi production
infra/                                  Docker, Nginx, Postgres, Redis, Prometheus, Grafana, Loki
.github/scripts + workflows             CI safety checks
```

---

## 3. Module-by-module audit

### 3.1 `services/trading-core/trading_core/runtime/bot_runtime.py`

**Vai trò hiện tại:** runtime trung tâm của bot: khởi tạo engines, chạy loop, tạo signal, gọi brain, build trade signal, pre-execution gate, reserve idempotency, build `ExecutionCommand`, gọi execution engine, emit events, mở trade, sync lifecycle, start reconciliation worker.

**Điểm mạnh:**

- Đã fail-closed nhiều đoạn trong live mode: thiếu daily state, thiếu broker account info, thiếu instrument spec, thiếu idempotency service, stale daily state, missing SL.
- Đã dùng `ExecutionCommand` thay vì `OrderRequest` trực tiếp trong live.
- Đã tạo `gate_ctx` có `symbol`, `side`, `account_id`, `broker_name`, `starting_equity`, `policy_version`, `idempotency_key`.
- Đã gọi `hash_gate_context(gate_ctx)` và đưa vào `PreExecutionContext`.
- Có cập nhật trạng thái idempotency: `reserved`, `broker_submitted`, `broker_unknown`, `rejected`, `filled`.
- Có emit `order_unknown` khi submit timeout/unknown receipt.

**Vấn đề P0:**

1. **File quá lớn và quá nhiều trách nhiệm.** Đây là điểm rủi ro lớn nhất về maintainability. Một file runtime đang ôm: brain, signal, sizing, risk, gate, ledger, broker submit, receipt, reconciliation, daily lock.
2. **Broker identity có nguy cơ sai:** `ExecutionEngine(provider_name=self.bot_instance_id)` trong `_init_engines()`, nhưng frozen context validate `broker_name == provider_name`. Nếu `broker_name=ctrader` nhưng provider_name là UUID bot, live order sẽ bị block hoặc dev phải bypass.
3. **Spread live quote failure bị nuốt:** trong live, lỗi `get_quote` bị `except Exception: pass`; gate dùng `spread_pips` default. Với live, quote/spread không xác thực phải block ngay.
4. **Policy version có thể rỗng:** `gate_ctx["policy_version"]` lấy từ signal meta. Nếu brain không set policy version, frozen context sẽ block ở execution stage, sau khi đã reserve idempotency/gate evaluated. Nên block sớm trước reservation.
5. **Order payload dùng `result.order_id` làm broker trade id.** Với broker thật, order id, position id, deal id có thể khác nhau. Cần mapping rõ: `broker_order_id`, `broker_position_id`, `broker_deal_id`, `trade_id`.
6. **`order_submitted` event phát trước broker call.** Tốt cho audit, nhưng ledger transition cần phân biệt `BROKER_SUBMITTING` và `BROKER_ACKED`; hiện dễ nhầm “submitted” với “accepted”.
7. **Daily lock action mới xử lý khi evaluate lock trả locked, nhưng runtime action close/stop không được gọi trực tiếp trong `BotRuntime`; phụ thuộc hook/service ở API.** Cần guarantee event persistence + action completion.

**Patch đề xuất:**

- Tách `bot_runtime.py` thành các module:

```text
trading_core/runtime/live_order_pipeline.py
trading_core/runtime/live_gate_context_builder.py
trading_core/runtime/live_daily_lock_guard.py
trading_core/runtime/live_receipt_handler.py
trading_core/runtime/live_reconciliation_orchestrator.py
trading_core/runtime/live_signal_executor.py
```

- Sửa `ExecutionEngine` identity:

```python
broker_name = getattr(self.broker_provider, "provider_name", "") or self._resolve_broker_identity()
self._execution_engine = ExecutionEngine(
    provider=self.broker_provider,
    provider_name=broker_name,
    runtime_mode=self.runtime_mode,
    ...
)
```

- Live quote fail-closed:

```python
if self.runtime_mode == "live":
    live_quote = await provider.get_quote(symbol)
    if not live_quote:
        self.state.error_message = "live_quote_unavailable"
        return
```

- Block sớm nếu policy version rỗng trước gate/reservation.
- Tách broker id mapping: không dùng `order_id` duy nhất cho trade.

---

### 3.2 `services/execution-service/execution_service/execution_engine.py`

**Vai trò:** route order qua provider, enforce live gate, verify idempotency reservation, verify frozen context hash, timeout broker submit.

**Điểm mạnh:**

- Live mode bắt buộc `ExecutionCommand`.
- Chặn provider không hỗ trợ client order id.
- Bắt buộc `brain_cycle_id`, `idempotency_key`, `pre_execution_context`, idempotency verifier.
- Verify reservation trong DB trước khi broker call.
- Verify `context_hash` từ `gate_context`.
- Gọi `validate_frozen_context_bindings()`.
- Timeout broker submit trả `submit_status=UNKNOWN`, `fill_status=UNKNOWN`.

**Vấn đề P0/P1:**

1. `provider_name` đang có nguy cơ truyền sai từ runtime như đã nêu.
2. Trong non-live vẫn tạo `gate_ctx` giả định `market_data_ok=True`, `confidence=1`, `rr=2`. Demo/paper được phép, nhưng không nên để code này gần live path.
3. Sau provider call, engine không verify receipt completeness; verify chính nằm ở `BotRuntime`. Nên đưa receipt validation vào execution-service để source-of-truth nằm gần broker.
4. `asyncio.wait_for` bắt `TimeoutError`, nhưng trong Python nên bắt `asyncio.TimeoutError` để rõ ràng.

**Patch đề xuất:**

- Thêm `validate_live_execution_receipt(result, request, command)` trong execution-service.
- Engine phải trả `OrderResult(success=False, submit_status="UNKNOWN")` nếu receipt không đủ broker id / account id / server time / raw hash.
- Không cho `provider.mode in {stub, paper, demo}` nếu `runtime_mode=live`.

---

### 3.3 `services/execution-service/execution_service/providers/base.py`

**Vai trò:** contract chuẩn broker provider.

**Điểm mạnh:**

- Có `OrderRequest`, `OrderResult`, `ExecutionReceipt`, `AccountInfo`, `PreExecutionContext`, `ExecutionCommand`.
- Optional live methods có fail bằng `NotImplementedError`.
- `supports_client_order_id` mặc định false.

**Thiếu cần bổ sung:**

- `AccountInfo` thiếu `account_id`, `server_time`, `raw_response_hash`, `source`.
- `OrderResult` cần thêm `requested_volume`, `filled_volume`, `reject_code`, `broker_status`, `client_order_id_verified`.
- `BrokerProvider` cần contract:

```python
async def validate_live_credentials() -> ProviderHealth
async def get_account_snapshot() -> AccountSnapshot
async def get_symbol_contract(symbol) -> InstrumentSpec
async def get_position_by_id(position_id)
async def get_order_status(broker_order_id)
async def get_order_by_client_order_id(client_order_id)
```

**Patch:** tạo `execution_service/contracts/live_broker_contract.py` và bắt CI verify mọi provider live implement đủ.

---

### 3.4 `services/execution-service/execution_service/providers/ctrader.py`

**Vai trò:** cTrader provider wrapper.

**Điểm mạnh:**

- Live mode fail nếu thiếu account id, execution adapter, account mismatch, stream không ready.
- `supports_client_order_id=True`.
- Có methods live-required: `get_instrument_spec`, `estimate_margin`, `get_order_by_client_id`, `get_executions_by_client_id`, `close_all_positions`, `get_server_time`, `get_quote`.
- `get_quote` live fail nếu không có broker quote, không fallback candle.

**Vấn đề P0:**

1. Provider vẫn wrap `trading_core.engines.ctrader_provider.CTraderDataProvider`, nơi còn fallback/demo behavior. Cần tách `CTraderOpenApiLiveProvider` riêng.
2. `connect()` kiểm market stream bằng candle `get_candles(limit=1)`, nhưng live quote readiness nên dùng broker tick/quote thật.
3. `get_order_by_client_id` live fallback search history nếu provider không có native lookup. Với live, fallback history không đủ để xác minh pending order ngay lập tức; cần native lookup hoặc queue unknown.
4. `get_server_time()` fallback `time.time()` kể cả live nếu provider không có server time. Live nên fail hoặc mark degraded; không dùng local time giả broker server time.
5. `place_order()` cần đảm bảo `client_order_id/idempotency_key` được gửi thật vào broker field có thể lookup. Nếu broker không hỗ trợ, phải fail closed.
6. `account_id` trong `OrderResult` phải được set đầy đủ để ledger trace.

**Patch:**

```text
services/execution-service/execution_service/providers/ctrader_live.py
services/execution-service/execution_service/providers/ctrader_mapper.py
services/execution-service/execution_service/providers/ctrader_errors.py
services/execution-service/tests/test_ctrader_live_contract.py
```

Live provider bắt buộc:

- Không fallback local server time.
- Không fallback history cho immediate ACK verification nếu native lookup thiếu.
- Submit order phải lưu `client_order_id` vào broker request và verify lại sau ACK.
- Mọi raw response phải hash.

---

### 3.5 `services/execution-service/execution_service/providers/bybit.py` và `mt5.py`

**Vai trò:** provider crypto và MT5.

**Điểm mạnh:** có skeleton provider.

**Rủi ro:** grep cho thấy còn thông báo `stub/paper mode only`, nhiều `pass`, fallback SDK unavailable. Với live, phải tuyệt đối không tự degrade sang stub.

**Patch:**

- Tách class live và paper rõ:

```text
BybitLiveProvider
BybitPaperProvider
MT5LiveProvider
MT5PaperProvider
```

- Registry provider phải reject live nếu class là paper/stub.
- CI `verify_no_live_stub_provider.py` phải scan cả string fallback/pass path.

---

### 3.6 `services/execution-service/execution_service/order_state_machine.py`

**Vai trò:** validate order transitions.

**Điểm mạnh:** đã được gọi ở API hook trước khi ledger transition.

**Cần hoàn thiện:**

- Chuẩn state nên là:

```text
INTENT_CREATED
GATE_ALLOWED
IDEMPOTENCY_RESERVED
BROKER_SUBMITTING
BROKER_ACKED
PARTIALLY_FILLED
FILLED
REJECTED
UNKNOWN
RECONCILING
RECONCILED_FILLED
RECONCILED_REJECTED
FAILED_NEEDS_OPERATOR
CANCELLED
CLOSED
```

- Không cho nhảy `GATE_ALLOWED -> FILLED` nếu thiếu `BROKER_ACKED`.
- Unknown phải có SLA và operator lock.

---

### 3.7 `services/execution-service/execution_service/reconciliation_worker.py`

**Vai trò:** sync broker positions/trades và resolve unknown orders.

**Điểm mạnh:**

- Có hook `get_unknown_order_attempts`, `on_unknown_resolved`.
- Có `resolve_unknown_orders()` gọi `UnknownOrderReconciler`.
- Có ghost position detection logic ở mức broker vs DB.

**Vấn đề P0:**

1. Worker chủ yếu chạy trong runtime; thiếu background daemon độc lập có lease/lock để xử lý unknown sau crash/restart.
2. Nếu runtime chết sau `order_unknown`, queue vẫn cần được worker hệ thống xử lý.
3. Cần dead-letter và incident escalation theo số lần retry/time since unknown.
4. Cần close/open position policy cho ghost position rõ ràng: auto-close hay operator review.

**Patch:**

```text
apps/api/app/workers/reconciliation_daemon.py
apps/api/app/services/reconciliation_lease_service.py
apps/api/app/services/reconciliation_deadletter_service.py
```

Rules:

- Unknown order > 30s: retry.
- Unknown order > 5 phút hoặc 3 lần fail: daily lock + critical incident.
- Ghost live position không có DB ledger: lock bot, không auto trade tiếp.

---

### 3.8 `services/execution-service/execution_service/unknown_order_reconciler.py`

**Vai trò:** lookup order/executions theo `client_order_id`.

**Điểm mạnh:** đúng hướng: dùng `idempotency_key/client_order_id` làm source để dò broker.

**Cần bổ sung:**

- Phân loại kết quả:

```text
FOUND_ORDER_ACKED
FOUND_FILLED
FOUND_REJECTED
NOT_FOUND_BUT_SAFE
NOT_FOUND_UNSAFE
LOOKUP_FAILED
MULTIPLE_MATCHES
```

- Nếu multiple matches cùng client id → critical incident, vì đây là lỗi idempotency nghiêm trọng.
- Nếu not found nhưng broker submit timeout trước đó → không được coi safe ngay; phải retry theo broker settlement window.

---

### 3.9 `services/trading-core/trading_core/runtime/pre_execution_gate.py`

**Vai trò:** hard gate trước order.

**Điểm mạnh:**

- Chặn live stub provider.
- Chặn broker disconnected, market data stale, daily loss, portfolio daily loss, daily TP, consecutive losses.
- Chặn margin usage, free margin, exposure, correlated USD exposure, slippage, spread, max positions.
- Chặn duplicate idempotency.
- Hash context đã bind thêm `symbol`, `side`, `account_id`, `broker_name`, `starting_equity`, `slippage_pips`, `policy_version`, `idempotency_key`.

**Vấn đề:**

1. `daily_take_profit_target` dùng `resolve_daily_take_profit_target(context)` nhưng cần chứng minh snapshot policy không bị thay đổi sau gate.
2. `policy_version_approved` chỉ bool; cần bind approved policy id/hash.
3. Gate không tự biết `broker_order_mode`: netting/hedging/FIFO. Với MT5/cTrader khác nhau, risk/exposure khác.
4. `correlated_usd_exposure_pct` hiện là proxy từ symbol chứa USD; cần correlation engine thật.

**Patch:**

- Thêm `policy_hash`, `risk_model_version`, `instrument_spec_hash`, `account_snapshot_hash` vào gate context.
- Gate hash phải bind toàn bộ snapshot dùng để approve order.

---

### 3.10 `services/trading-core/trading_core/runtime/frozen_context_contract.py`

**Vai trò:** validate immutable context giữa gate và execution.

**Điểm mạnh:**

- Bắt bot id, idempotency, brain cycle, account id, broker name, policy version.
- Validate idempotency in gate context.
- Validate symbol, side, requested volume, order type, price band, SL/TP.
- Verify `context_hash` lại lần nữa.

**Vấn đề:**

- File có đoạn code duplicate sau `return FrozenContextValidationResult(True, "ok")`; phần sau unreachable. Không gây lỗi runtime nhưng là debt nghiêm trọng, dễ drift.
- Price band 5% cho entry price là quá rộng với Forex scalping. Live nên dùng slippage/spread/pip band theo symbol.
- Nếu `gate_context` thiếu `symbol`/`side`, code fallback sang request nên có thể không phát hiện gate thiếu field. Với live, thiếu field trong frozen context phải block.

**Patch:**

- Xóa unreachable duplicate block.
- Live required fields: `symbol`, `side`, `requested_volume`, `account_id`, `broker_name`, `policy_version`, `idempotency_key`, `starting_equity`, `instrument_spec_hash`.
- Entry price tolerance = `max_slippage_pips * pip_size`, không phải 5%.

---

### 3.11 `services/trading-core/trading_core/risk/risk_context_builder.py`

**Vai trò:** build margin/exposure/max loss context.

**Điểm mạnh:**

- Live bắt buộc instrument spec thật; không fallback.
- Live bắt buộc `broker_margin_required > 0`.
- Tính max loss theo SL, pip value, volume.
- Tính margin usage, free margin after order.

**Vấn đề P1:**

1. Exposure tính bằng notional/equity đơn giản; forex leverage/margin/currency conversion chưa đủ.
2. `open_positions` dùng volume/price từ dict và contract_size hiện tại; nếu position có symbol khác, contract size/pip/margin khác thì sai.
3. Correlation USD chỉ là proxy `if "USD" in symbol`; chưa đủ cho portfolio risk.
4. Chưa kiểm min/max lot/lot step ở đây; sizing có nhưng risk context nên validate lại.
5. `pip_value_per_lot_usd` giả định USD; account currency khác USD cần conversion.

**Patch:**

```text
trading_core/risk/portfolio_exposure_engine.py
trading_core/risk/currency_conversion.py
trading_core/risk/correlation_bucket_engine.py
trading_core/risk/broker_position_normalizer.py
```

Live risk phải lấy:

- broker margin estimate
- broker free margin
- broker position list normalized
- instrument spec per symbol
- account currency conversion

---

### 3.12 `services/trading-core/trading_core/risk/position_sizing.py`

**Vai trò:** tính lot theo risk pct.

**Điểm mạnh:** có risk pct, pip size, pip value, min/max/step.

**Cần hoàn thiện:**

- Live phải dùng broker lot step/min/max từ instrument spec.
- Nếu calculated lot bị round lên vượt risk, phải round xuống.
- Nên output reason/debug:

```python
PositionSizingResult(lot, risk_amount, stop_pips, rounded_by, capped_by, warnings)
```

---

### 3.13 `apps/api/app/services/live_start_preflight.py`

**Vai trò:** chặn start live nếu thiếu điều kiện.

**Điểm mạnh:**

- Check provider live readiness.
- Check active policy approved.
- Validate required live policy keys.
- Sync broker equity và recompute daily state, fail-closed.
- Block daily lock, unresolved unknown orders, open critical incidents.

**Cần bổ sung P0/P1:**

- Check provider `supports_client_order_id`.
- Check provider `get_quote`, `get_instrument_spec`, `estimate_margin`, `get_order_by_client_id`, `get_executions_by_client_id` thật sự hoạt động với symbol bot.
- Check server time drift.
- Check broker account id match bot connection.
- Check latest migration version == head.
- Check legacy backend disabled in production.
- Check reconciliation worker/daemon healthy.

---

### 3.14 `apps/api/app/services/daily_profit_lock_engine.py` + `daily_lock_runtime_controller.py` + `daily_trading_state.py`

**Vai trò:** daily TP/loss state và runtime lock action.

**Điểm mạnh:**

- Có daily state service.
- Có lock event ledger.
- Có controller cho `stop_new_orders`, `close_all_and_stop`, `reduce_risk_only`.
- `close_all_positions` verify remaining positions sau close.

**Vấn đề:**

1. Runtime controller không tự persist DB; phụ thuộc caller. Nếu controller action fail giữa chừng, trạng thái DB/operator có thể lệch.
2. `stop_new_orders` set runtime metadata, nhưng nếu process restart, metadata mất; DB daily lock vẫn block nếu preflight/gate đọc đúng, nhưng UI/runtime cần restore state khi create runtime.
3. `close_all_and_stop` cần receipt/ledger cho từng close order.
4. Lock action cần idempotency key riêng: `daily_lock:{bot_id}:{date}:{reason}`.

**Patch:**

- `DailyLockOrchestrator` atomic:

```text
lock_day -> emit event -> apply runtime action -> verify positions -> persist action result -> incident if failed
```

- Khi runtime start, load daily lock DB và set `new_orders_paused` ngay.

---

### 3.15 `apps/api/app/services/order_ledger_service.py`

**Vai trò:** source-of-truth cho order attempts/receipts/projection.

**Điểm mạnh:**

- Có `persist_intent`.
- Có `persist_execution_receipt_and_projection`.
- Có `enqueue_unknown_order`.
- Có `record_lifecycle_event` coordination.

**Vấn đề:**

1. Chưa thấy đảm bảo transaction atomic giữa receipt, attempt update, projection, enqueue unknown nếu caller commit từng service bên ngoài.
2. `order_submitted` hiện có thể gọi `persist_intent` lại, trong khi gate allowed đã tạo intent; cần tránh duplicate semantics.
3. Source-of-truth nên là ledger immutable + projection derived. Hiện service vừa update attempt vừa projection, cần ranh giới rõ hơn.

**Patch:**

- Dùng một DB transaction cho lifecycle event.
- Tạo immutable table `order_events` và projection rebuildable.
- `orders_projection` không được write trực tiếp từ runtime payload ngoài ledger service.

---

### 3.16 `apps/api/app/services/safety_ledger.py`

**Vai trò:** brain cycle, gate, broker order events, daily lock, state transitions.

**Điểm mạnh:** trung tâm audit tốt.

**Cần hoàn thiện:**

- Thêm `event_hash`, `previous_event_hash` để tạo tamper-evident audit chain.
- Thêm `source_component`, `runtime_version`, `git_sha`, `policy_hash`.
- Ledger events phải append-only; updates chỉ ở projection/attempt table.

---

### 3.17 `apps/api/app/services/reconciliation_queue_service.py`

**Vai trò:** queue unknown order.

**Điểm mạnh:** có pending/retry/resolved/failed_needs_operator; preflight check unresolved.

**Cần bổ sung:**

- Lease/in_progress owner để tránh 2 worker xử lý cùng item.
- Retry backoff exponential.
- `max_attempts`, `first_seen_at`, `deadline_at`.
- Dead-letter table.
- API operator: force resolve / mark false positive / close broker position / attach evidence.

---

### 3.18 `apps/api/app/services/bot_service.py`

**Vai trò:** bridge API DB hooks vào runtime.

**Điểm mạnh:**

- Hooks persist signal/trade/snapshot/events.
- Gate allowed tạo order attempt.
- Order filled/rejected/unknown persist receipt/projection, enqueue unknown, incident.
- Unknown resolved callback có xử lý queue, transition, incident/daily lock.
- Live start có preflight.

**Vấn đề:**

1. File quá lớn, nhiều hook closure phức tạp; khó test đầy đủ.
2. Một số event failure ở live raise RuntimeError, nhưng các chỗ `_safe_hook` trong runtime có thể nuốt lỗi tùy event. Cần phân loại required vs optional events rõ.
3. `on_order` không persist trực tiếp, chỉ publish; nhưng `order_submitted` event đã persist ở `on_event`. Cần docs rõ để dev không ghi thêm lần nữa.

**Patch:**

Tách:

```text
app/services/runtime_hooks/order_hooks.py
app/services/runtime_hooks/daily_hooks.py
app/services/runtime_hooks/reconciliation_hooks.py
app/services/runtime_hooks/signal_hooks.py
app/services/runtime_hooks/hook_factory.py
```

---

### 3.19 `apps/api/app/routers/live_trading.py`

**Vai trò:** endpoints live control.

**Điểm mạnh:** có reset daily lock, reconcile, kill switch, runtime actions.

**Cần bổ sung:**

- Endpoint `POST /live/{bot}/preflight/dry-run` trả toàn bộ check chi tiết.
- Endpoint `POST /live/{bot}/force-close-all` với approval reason.
- Endpoint `POST /live/{bot}/unknown-orders/{id}/operator-resolve`.
- RBAC: live start/kill/reset lock/force close phải cần role operator/admin + audit reason.

---

### 3.20 `apps/web/` frontend

**Điểm mạnh:**

- Đã có Live Control Center tiếng Việt.
- Đã có DailyLockPanel, ExecutionReceiptDrawer, LiveReadinessPanel, ReconciliationTimeline, UnknownOrdersPanel.
- Có live orders/runtime-control.

**Thiếu:**

- UI readiness chưa đủ check preflight thật: policy approval, provider contract, server time, unresolved queue, account snapshot freshness, migration head.
- UnknownOrdersPanel đang dựa vào incidents/runs; nên có API queue items thật.
- Cần “red button” force lock/close all với confirmation 2 bước và reason.
- Cần hiển thị order lifecycle timeline từ ledger: intent → gate → reserve → submitted → ack/unknown → reconciled.

---

### 3.21 `ai_trading_brain/`

**Vai trò:** brain/gov/memory/evolution.

**Điểm mạnh:** có separation AI brain khỏi execution.

**Điểm phải khóa:**

- AI brain không được trực tiếp quyết định lot/order submit trong live.
- Brain chỉ output intent + confidence + rationale + policy snapshot id.
- Execution/risk/gate mới là hard authority.

**Patch:**

- `BrainDecisionContract` bắt buộc có:

```json
{
  "action": "BUY|SELL|HOLD",
  "confidence": 0.0,
  "symbol": "EURUSD",
  "policy_version": "...",
  "risk_intent": "normal|reduce_only",
  "rationale_hash": "..."
}
```

- Nếu thiếu policy_version → live block trước gate.

---

### 3.22 `backend/` legacy và `frontend/` legacy

**Vấn đề lớn:** Repo vẫn còn `backend/main.py`, `backend/engine/*`, `frontend/app.py`. Đây là legacy stack đầy đủ. Dù đã có `README_DEPRECATED.md` và CI drift script, production vẫn cần hard isolation.

**Patch P0:**

- Production Docker không mount/run `backend/` và `frontend/` legacy.
- CI fail nếu `apps/api` import từ `backend`.
- Move legacy vào `legacy/` hoặc `archive/` và exclude package discovery.
- Add `pytest` test:

```python
def test_no_production_imports_legacy_backend(): ...
```

---

## 4. Critical P0 patch order

### P0.1 — Broker Identity + Frozen Context Fix

**Mục tiêu:** sửa mismatch `provider_name=self.bot_instance_id`.

Files:

```text
services/trading-core/trading_core/runtime/bot_runtime.py
services/execution-service/execution_service/execution_engine.py
services/trading-core/tests/test_frozen_context_contract.py
services/execution-service/tests/test_execution_engine_live_identity.py
```

Acceptance:

- Live provider_name = broker identity (`ctrader`, `mt5`, `bybit`).
- bot id vẫn nằm trong context nhưng không dùng làm broker name.
- Frozen context reject broker mismatch thật.

---

### P0.2 — Live Quote / Spread Fail-Closed

Files:

```text
services/trading-core/trading_core/runtime/live_gate_context_builder.py
services/trading-core/trading_core/runtime/bot_runtime.py
services/execution-service/execution_service/providers/ctrader.py
```

Acceptance:

- Live thiếu quote → BLOCK.
- Không dùng provider default spread nếu quote fail.
- Spread phải từ broker bid/ask.

---

### P0.3 — Execution Receipt Contract Hardening

Files:

```text
services/execution-service/execution_service/parity_contract.py
services/execution-service/execution_service/execution_engine.py
services/execution-service/execution_service/providers/base.py
apps/api/app/services/order_ledger_service.py
```

Acceptance:

- Live success phải có submit_status ACKED, fill_status FILLED/PARTIAL, broker_order_id hoặc broker_position_id, account_id, raw_response_hash.
- Nếu receipt thiếu → `order_unknown`, không mở trade.

---

### P0.4 — cTrader Live Provider Split

Files:

```text
services/execution-service/execution_service/providers/ctrader_live.py
services/execution-service/execution_service/providers/ctrader.py
services/execution-service/execution_service/providers/__init__.py
```

Acceptance:

- Live class không fallback local time/candle/history nếu native lookup thiếu.
- Demo/paper fallback chỉ nằm ở demo/paper class.

---

### P0.5 — Unknown Order Daemon

Files:

```text
apps/api/app/workers/reconciliation_daemon.py
apps/api/app/services/reconciliation_queue_service.py
apps/api/app/services/reconciliation_lease_service.py
apps/api/app/routers/live_trading.py
```

Acceptance:

- Unknown orders được xử lý sau restart.
- Có lease, retry, dead-letter, incident.
- Preflight block nếu unresolved.

---

### P0.6 — Daily Lock Orchestrator

Files:

```text
apps/api/app/services/daily_lock_orchestrator.py
apps/api/app/services/daily_profit_lock_engine.py
apps/api/app/services/daily_lock_runtime_controller.py
services/trading-core/trading_core/runtime/bot_runtime.py
```

Acceptance:

- Daily TP/loss hit → DB locked → runtime paused/close all → action result persisted.
- Restart bot vẫn đọc DB lock và block.
- Close-all action có per-position receipt.

---

### P0.7 — Legacy Production Isolation

Files:

```text
.github/scripts/verify_production_no_legacy_stack.sh
.github/scripts/verify_live_import_boundary.py
infra/docker/docker-compose.prod.yml
apps/api/pyproject.toml
```

Acceptance:

- Production không import/run `backend/` hoặc `frontend/` legacy.
- CI fail nếu live code import legacy.

---

## 5. P1 patch order

1. Tách `bot_runtime.py` thành live pipeline modules.
2. Add portfolio exposure engine thật.
3. Add currency conversion service cho account currency.
4. Add policy hash/instrument hash/account snapshot hash vào gate context.
5. Add immutable event chain hash cho safety ledger.
6. Add operator APIs cho unknown order resolution.
7. Add UI lifecycle timeline từ ledger.
8. Add broker server time drift check.
9. Add load tests cho multi-bot runtime.
10. Add chaos tests: broker timeout, duplicate submit, partial fill, disconnected during close-all.

---

## 6. P2 patch order

1. Real backtest-to-live parity report.
2. Strategy sandbox / walk-forward validation.
3. Slippage model and execution quality analytics.
4. Risk-adjusted performance dashboard: Sharpe, PF, expectancy, max DD, ruin probability.
5. Cost model: spread/commission/swap.
6. Broker abstraction for netting/hedging/FIFO.
7. Multi-account capital allocation.
8. Alert escalation: Telegram/Discord/Email/Webhook.
9. Secret rotation and broker token refresh.
10. Compliance/export audit bundle.

---

## 7. Readiness checklist trước khi chạy tiền thật

Live chỉ được bật khi tất cả PASS:

```text
[ ] Production không chạy legacy backend/frontend.
[ ] Broker provider live không fallback stub/paper/demo.
[ ] Broker credentials/account id verified.
[ ] Broker quote, server time, account snapshot fresh.
[ ] Instrument spec từ broker thật.
[ ] Broker-native margin estimate available.
[ ] Daily state recomputed từ broker equity.
[ ] Daily TP/loss policy approved và có hash.
[ ] Unknown order queue empty.
[ ] No critical incident open.
[ ] Reconciliation daemon healthy.
[ ] Idempotency reservation service active.
[ ] Frozen gate context hash includes symbol/side/account/broker/policy/equity/spec.
[ ] Execution receipt contract enforced.
[ ] Partial fill handling tested.
[ ] Timeout/unknown order path tested.
[ ] Close-all daily lock path tested.
[ ] UI operator sees lifecycle timeline and can kill/reconcile.
[ ] CI no-stub/no-fallback/no-legacy checks required in release workflow.
```

---

## 8. Đánh giá theo chuẩn live trading production

| Hạng mục | Trạng thái | Ghi chú |
|---|---:|---|
| Monorepo architecture | 8/10 | Rõ ràng, nhưng còn legacy stack |
| Runtime engine | 7/10 | Mạnh nhưng quá lớn, cần tách pipeline |
| Execution engine | 7.5/10 | Gate tốt, cần receipt validation trong service |
| Broker live readiness | 6/10 | cTrader tốt hơn, nhưng cần live provider split |
| Risk engine | 6.5/10 | Có broker-native margin, exposure còn đơn giản |
| Daily TP/loss lock | 7/10 | Có state/controller, cần orchestrator atomic |
| Order ledger | 7.5/10 | Đúng hướng, cần immutable chain và transaction rõ |
| Unknown reconciliation | 7/10 | Có queue/reconciler, cần daemon/lease/dead-letter |
| Frontend operator | 7/10 | Có panel, cần queue/lifecycle/preflight thật hơn |
| CI safety | 8/10 | Nhiều script tốt, cần bắt buộc trong release gate |
| Production deploy | 6.5/10 | Infra có, cần legacy isolation/secrets/worker health |

**Tổng readiness:** khoảng **72/100** cho production engineering. Muốn chạy live thật an toàn nên đạt **90/100+**.

---

## 9. Bước tiếp theo mạnh nhất

Nên build ngay patch:

# BUILD P0 LIVE EXECUTION TRUTH PATCH

Mục tiêu của patch này:

1. Sửa broker identity/frozen context mismatch.
2. Fail-closed live quote/spread/server time.
3. Đưa receipt validation vào execution-service.
4. Split cTrader live provider khỏi fallback/demo.
5. Bắt unknown order vào daemon có lease/retry/dead-letter.
6. Daily lock orchestrator atomic.
7. Production no-legacy hard gate.

Sau patch này mới nên chuyển sang:

# BUILD P1 RISK + PORTFOLIO HARDENING PATCH

với broker-native exposure, currency conversion, policy hash, instrument hash, account snapshot hash.
