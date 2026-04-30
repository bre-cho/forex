# FOREX-MAIN-7(1) — Deep File/Module/Engine Audit & Completion Plan

**Artifact audited:** `forex-main-7(1).zip`  
**Audit mode:** static code review, file/module inventory, production-readiness check for real live Forex/Crypto trading.  
**Final verdict:** **CHƯA NÊN CHẠY TIỀN THẬT KHÔNG GIÁM SÁT.** Repo đã tiến bộ mạnh so với các bản trước: có monorepo, API mới, runtime registry, pre-execution gate, frozen context, daily lock, order ledger, execution receipt, reconciliation queue/daemon, UI live panels và nhiều verify scripts. Tuy nhiên vẫn còn một số điểm P0 có thể gây rủi ro tiền thật: Docker root vẫn trỏ legacy stack, `CTraderLiveProvider` wrapper có logic live/demo ngược dễ gây nhầm, receipt/order lifecycle chưa thật sự atomic end-to-end, policy hash đang có fallback `policy_hash_unknown`, quote/instrument hashes chưa đủ source-of-truth, và live runtime còn phụ thuộc nhiều hook optional.

---

## 1. Inventory tổng quan

Repo có khoảng **352 file** trong zip. Cấu trúc chính:

| Khu vực | Vai trò | Nhận xét production |
|---|---|---|
| `apps/api` | FastAPI production API, SQLAlchemy, Alembic, routers, services, workers | Đây là stack mới nên dùng làm production path. Có migrations `0001` → `0018`. |
| `apps/web` | Next.js web dashboard | Có live panels: readiness, receipt, reconciliation, unknown orders, daily lock. |
| `apps/admin` | Admin console | Có broker health, runtime, users, workspaces, operations dashboard. |
| `services/trading-core` | Core trading engines, runtime, risk, gates | Đây là brain/runtime core. Có `BotRuntime`, `RuntimeRegistry`, `PreExecutionGate`, `RiskContextBuilder`. |
| `services/execution-service` | Broker adapters, execution engine, order state machine, reconciliation | Đây là execution plane. Có provider split demo/live/paper. |
| `ai_trading_brain` | AI decision brain layer | Có brain contracts/runtime/decision/evolution/governance. |
| `backend/` + `frontend/` | Legacy FastAPI/Streamlit stack | Vẫn còn trong repo và `docker-compose.yml` root đang trỏ vào legacy. Cần cô lập khỏi production. |
| `.github/scripts` | Verification scripts | Có nhiều guard tốt: no live stub, live import boundary, broker gate wiring, production no legacy stack. |
| `infra/` | Docker/monitoring/nginx/postgres/redis | Có prod compose riêng nhưng root compose vẫn legacy. |

---

## 2. Điểm tiến bộ lớn ở bản 7(1)

1. **Đã có runtime multi-bot chuẩn hơn**: `RuntimeRegistry` quản lý từng `BotRuntime`, có start/stop/pause/resume và lock theo bot.
2. **Live mode đã bị ép qua `ExecutionCommand`**: `ExecutionEngine.place_order()` chặn `OrderRequest` thô trong live.
3. **Đã có frozen gate context**: `GateContextV1`, `hash_gate_context()`, `validate_frozen_context_bindings()` bind symbol/side/account/broker/policy/idempotency/volume/SL/TP.
4. **Đã có broker capability proof**: `BrokerCapabilityProof` kiểm tra account, quote, server time, instrument spec, client order id, order lookup, close all.
5. **Đã có daily TP/loss state**: `DailyTradingState`, `DailyLockEvent`, `DailyLockAction`, `DailyLockRuntimeController`, `DailyProfitLockEngine`.
6. **Đã có unknown order queue + daemon**: `ReconciliationQueueItem`, lease, retry/backoff, dead-letter, incident, daily lock.
7. **Đã có broker-native risk context bước đầu**: `RiskContextBuilder` yêu cầu live có instrument spec và broker margin estimate.
8. **Đã có execution receipt model**: `BrokerExecutionReceipt` lưu broker id, position id, deal id, submit/fill status, raw response hash.
9. **Đã có frontend live panels**: giúp operator nhìn readiness, execution receipts, unknown orders, reconciliation timeline, daily lock.

---

## 3. P0 findings — lỗi/rủi ro phải sửa trước khi live tiền thật

### P0.1 — Root `docker-compose.yml` vẫn chạy legacy backend/frontend

**File:** `docker-compose.yml`  
**Hiện trạng:** root compose build `./backend` và `./frontend` legacy, không phải `apps/api` + `apps/web` + services production. Trong khi `backend/README_DEPRECATED.md` cho thấy đây là stack cũ.

**Rủi ro:** dev/operator chạy nhầm lệnh root compose sẽ bật legacy SQLite/Streamlit path, bypass production API, migration, order ledger, runtime registry và live safety closure.

**Patch bắt buộc:**
- Đổi root `docker-compose.yml` thành production/dev monorepo compose, hoặc xóa/đổi tên thành `docker-compose.legacy.yml`.
- Root compose chỉ được chạy: postgres, redis, `apps/api`, workers/daemon, `apps/web`, `apps/admin`, nginx/prometheus nếu cần.
- Thêm CI bắt buộc fail nếu root compose còn `context: ./backend` hoặc `context: ./frontend`.

**Acceptance:**
```bash
grep -R "context: ./backend\|context: ./frontend" docker-compose.yml && exit 1 || true
```

---

### P0.2 — `CTraderLiveProvider` wrapper đang set `kwargs["live"] = False` rồi mới gán `self.live = True`

**File:** `services/execution-service/execution_service/providers/ctrader_live.py`

**Hiện trạng:**
```python
kwargs["live"] = False
super().__init__(*args, **kwargs)
self.live = True
```

Base `CTraderProvider.__init__()` chặn `live=True`, nên wrapper ép false rồi đổi sau. Cách này chạy được về mặt object state, nhưng **mùi kiến trúc rất nguy hiểm** vì live/demonstration contract bị đảo logic. Base class vẫn là demo-only, nhưng subclass lách qua bằng mutation.

**Rủi ro:**
- Dễ tạo provider live nhưng config/adapter vẫn khởi tạo theo demo assumption.
- Khó audit: provider được tạo qua class live nhưng constructor base chạy demo-only branch.
- Nếu sau này base thêm logic phụ thuộc `live` trong `__init__`, live wrapper có thể âm thầm sai.

**Patch bắt buộc:**
- Tách `BaseCTraderProvider` dùng chung.
- `CTraderDemoProvider` và `CTraderLiveProvider` truyền mode rõ ràng, không mutation sau init.
- `CTraderLiveProvider.__init__()` phải hard-pin `live=True`, `mode="live"`, `provider_name="ctrader"`, `environment="live"`.
- CI check: cấm pattern `kwargs["live"] = False` trong `*_live.py`.

---

### P0.3 — Live provider nói `supports_client_order_id=True` nhưng cTrader order submit chưa truyền client id thật vào adapter

**File:** `services/execution-service/execution_service/providers/ctrader.py`

Trong `place_order()`, live kiểm tra `request.client_order_id`, nhưng khi gọi adapter:
```python
result = await self._execution_adapter.place_market_order(
    symbol=request.symbol,
    side=request.side,
    volume=request.volume,
    stop_loss=request.stop_loss,
    take_profit=request.take_profit,
    comment=request.comment,
)
```
Chỉ truyền `comment`, không truyền field rõ ràng như `client_order_id`/`clientMsgId` nếu adapter hỗ trợ.

**Rủi ro:** unknown-order reconciliation đang dựa vào `get_order_by_client_id(idempotency_key)`. Nếu broker không thật sự lưu client id hoặc comment không ổn định, reconcile có thể không tìm được lệnh đã khớp.

**Patch bắt buộc:**
- Cập nhật `CTraderExecutionAdapter.place_market_order()` nhận `client_order_id` bắt buộc trong live.
- Mapping sang broker-native `clientMsgId`/label/comment theo cTrader API implementation thật.
- `supports_client_order_id=True` chỉ được trả về nếu adapter proof xác nhận broker echo lại id này trong order/deal/history.
- Test live contract: submit dry-run/sandbox order có `client_order_id`, lookup lại bằng `get_order_by_client_id()` phải trả đúng order.

---

### P0.4 — `policy_hash` đang fallback thành `policy_hash_unknown`

**File:** `services/trading-core/trading_core/runtime/bot_runtime.py`

Trong gate context:
```python
"policy_hash": str(self.state.metadata.get("policy_hash") or "policy_hash_unknown")
```
`validate_frozen_context_bindings()` chỉ check non-empty, nên `policy_hash_unknown` vẫn pass.

**Rủi ro:** live order có thể được phép khi policy snapshot chưa được hash/approved thật. Đây là Trust Break Point lớn trong risk governance.

**Patch bắt buộc:**
- Trong live: nếu không có `policy_hash` thật từ approved policy snapshot → BLOCK.
- `policy_hash` phải là SHA256 của canonical active policy snapshot từ DB.
- `policy_version_approved=True` không được hardcode; phải lấy từ `PolicyService` và bind vào `GateContextV1`.
- `PreExecutionGate` nên block `policy_hash in {"", "policy_hash_unknown"}` trong live.

---

### P0.5 — `instrument_spec_hash`, `quote_id`, `quote_timestamp` chưa được bind thật

**File:** `services/trading-core/trading_core/runtime/bot_runtime.py`

Gate context có các field:
```python
"quote_id": "",
"quote_timestamp": 0.0,
"instrument_spec_hash": str(self.state.metadata.get("instrument_spec_hash") or ""),
```
Nhưng live path chưa tạo hash canonical từ broker spec/quote response.

**Rủi ro:** final order có thể không chứng minh được đã dùng quote/spec nào để tính spread, lot, margin. Khi dispute/slippage xảy ra, không replay được quyết định.

**Patch bắt buộc:**
- Sau `get_quote()`, tạo `quote_id = sha256(symbol,bid,ask,server_time,provider)` và `quote_timestamp` từ broker server time, không dùng local time nếu broker có.
- Sau `get_instrument_spec()`, tạo `instrument_spec_hash = sha256(canonical spec)`.
- Gate hash phải fail nếu live thiếu `quote_id`, `quote_timestamp`, `instrument_spec_hash`.

---

### P0.6 — `order_submitted` event đang emit trước khi broker thật ACK

**File:** `services/trading-core/trading_core/runtime/bot_runtime.py`

Trước khi gọi `ExecutionEngine.place_order(command)`, runtime emit:
```python
await self._emit_event("order_submitted", {...})
```
Sau đó `ExecutionEngine` mới mark submitting và gọi broker. Về ngữ nghĩa, event này dễ bị hiểu là broker submitted/acked, trong khi thực tế mới là local intent.

**Rủi ro:** order ledger/projection/operator UI có thể hiển thị sai trạng thái. Trong live trading, phân biệt `INTENT_RESERVED`, `SUBMIT_REQUESTED`, `BROKER_ACKED`, `FILLED`, `UNKNOWN` là bắt buộc.

**Patch bắt buộc:**
- Đổi event trước broker thành `order_intent_reserved` hoặc `order_submit_requested`.
- Chỉ emit `order_submitted`/`broker_acked` khi receipt có `submit_status=ACKED`.
- Ledger state machine phải là source-of-truth; UI không suy diễn từ event tên mơ hồ.

---

### P0.7 — Atomic lifecycle chưa khép kín giữa runtime event và DB ledger

**Files:**
- `services/trading-core/trading_core/runtime/bot_runtime.py`
- `apps/api/app/services/order_ledger_service.py`
- `apps/api/app/services/safety_ledger.py`

Repo có `OrderLifecycleUnitOfWork`, nhưng runtime vẫn emit nhiều event và gọi hooks optional. Nếu hook/event persistence fail ở giữa, trạng thái runtime và DB có thể lệch.

**Rủi ro:**
- Broker đã nhận lệnh nhưng DB chưa có receipt.
- DB đã reserved nhưng không có queue unknown.
- `on_order`/`on_event` optional khiến live path phụ thuộc wiring bên ngoài.

**Patch bắt buộc:**
- Trong live mode, mọi hook lifecycle phải là required dependency: missing hook = fail-closed trước khi start.
- `ExecutionEngine.place_order()` không chỉ trả result; nó phải gọi một UnitOfWork persist result/unknown trong cùng abstraction.
- `BotRuntime` không được tự mở trade nếu ledger chưa persist receipt và projection success.

---

### P0.8 — Unknown Order daemon hiện chủ yếu retry/escalate queue, chưa thật sự gọi broker reconciler trong daemon path

**Files:**
- `apps/api/app/workers/reconciliation_daemon.py`
- `services/execution-service/execution_service/unknown_order_reconciler.py`
- `services/execution-service/execution_service/reconciliation_worker.py`

Daemon lấy pending items, lease, retry/backoff, dead-letter, incident, daily lock. Nhưng trong code daemon API-level, chưa thấy injection provider/runtime để gọi `UnknownOrderReconciler.resolve_unknown_order()` cho từng item.

**Rủi ro:** queue có thể tăng attempts/dead-letter mà không thật sự hỏi broker trước khi escalation. Nếu runtime-local worker chết, daemon cấp API không tự resolve được.

**Patch bắt buộc:**
- Daemon cần provider registry hoặc command bus để lấy broker provider theo bot.
- Mỗi pending item phải chạy: `provider.get_order_by_client_id()` → `get_executions_by_client_id()` → persist outcome.
- Nếu provider unavailable, mới mark retry. Nếu found filled/rejected, remove queue và update ledger/projection.
- Add test: enqueue unknown + mock provider returns filled → daemon resolves to FILLED without dead-letter.

---

### P0.9 — `RiskContextBuilder` exposure vẫn là notional/equity proxy, chưa tính netting/hedging/cross-currency đầy đủ

**File:** `services/trading-core/trading_core/risk/risk_context_builder.py`

Có broker margin estimate cho live, tốt. Nhưng exposure đang tính:
```python
notional = volume * contract_size * price
account_exposure = total_notional / equity * 100
```
Chưa thấy xử lý:
- account currency khác USD,
- FX conversion pip value theo quote/base/account currency,
- netting vs hedging mode,
- crypto contract inverse/linear,
- symbol correlation bucket thực tế.

**Rủi ro:** risk gate có thể block quá mức hoặc allow quá mức, đặc biệt cross pairs/JPY/gold/crypto.

**Patch bắt buộc:**
- Thêm `InstrumentSpec.asset_class`, `quote_currency`, `base_currency`, `contract_type`, `margin_currency`, `tick_value`, `tick_size`.
- Provider phải trả broker-native margin + pip/tick value trong account currency.
- Exposure guard phải tách `gross_exposure`, `net_exposure`, `symbol_exposure`, `currency_bucket_exposure`.

---

### P0.10 — Live start preflight chưa chứng minh root deployment đang dùng monorepo production path

**Files:**
- `apps/api/app/services/live_start_preflight.py`
- `docker-compose.yml`
- `infra/docker/docker-compose.prod.yml`

Preflight kiểm provider, policy, daily state, unknown orders, critical incidents. Nhưng deployment root vẫn có legacy compose. Cần preflight/config check chặn app production nếu legacy backend route/process còn enabled.

**Patch bắt buộc:**
- Health/deep trong production phải expose `production_stack=true`, `legacy_stack_absent=true`.
- CI release phải chạy `verify_production_no_legacy_stack.sh` trên compose chính.
- Root `Makefile` target mặc định phải dùng `infra/docker/docker-compose.dev.yml` hoặc production compose mới.

---

## 4. Module-by-module completion roadmap

### 4.1 `apps/api` — API, DB, services, workers

| File/module | Hiện trạng | Hoàn thiện cần làm |
|---|---|---|
| `app/main.py` | Có registry, daemon, health, legacy route guard. | Thêm startup check production compose/runtime identity; health phải fail nếu daemon enabled nhưng chết. |
| `app/models/__init__.py` | Đã có order attempts, receipts, daily state, queue, incidents. | Thêm unique cho receipt `(bot_instance_id,idempotency_key,broker_order_id,broker_deal_id)`; thêm event-sourced ledger sequence. |
| `alembic/versions/0001-0018` | Có migration tuần tự. | Thêm CI `alembic upgrade head` trên Postgres thật; cấm SQLite trong live tests. |
| `live_start_preflight.py` | Fail-closed equity sync, policy, unknown orders. | Thêm check capability proof age, quote freshness, production stack, required hooks wiring. |
| `daily_lock_runtime_controller.py` | Có daily lock action concept. | Exactly-once action: lock → pause_new_orders → optional close_all → verify no new order accepted. |
| `order_ledger_service.py` | Có UoW. | Runtime live phải dùng UoW trực tiếp cho mọi transition, không chỉ thông qua optional events. |
| `reconciliation_daemon.py` | Có lease/backoff/escalation. | Inject broker reconciler thật; resolve trước khi retry/dead-letter. |
| `risk_policy.py`, `policy_service.py` | Có policy approval. | Policy hash canonical + approval binding vào gate context. |

### 4.2 `services/trading-core` — runtime, gate, risk, strategy engines

| File/module | Hiện trạng | Hoàn thiện cần làm |
|---|---|---|
| `runtime/bot_runtime.py` | Live path tốt hơn, có quote/spec/risk/daily/gate/order. | Tách `_execute_signal()` thành pipeline nhỏ: quote → account → spec → sizing → risk → gate → reserve → execute → ledger. Hiện hàm quá dài, khó audit. |
| `runtime/pre_execution_gate.py` | GateContextV1 bind nhiều field. | Trong live, require non-empty real `policy_hash`, `quote_id`, `quote_timestamp`, `instrument_spec_hash`, `account_id`. |
| `runtime/frozen_context_contract.py` | Bind request/context/gate. | Thêm check `quote_timestamp` freshness, `approved_volume > 0`, `margin_required > 0`, `starting_equity > 0`. |
| `risk/risk_context_builder.py` | Có live broker margin required. | Broker-native tick/pip/exposure conversion cho Forex + Crypto. |
| `risk/position_sizing.py` | Tính lot theo risk. | Add broker min/max/step round-trip validation; block if rounded lot changes risk > threshold. |
| `data/market_data_quality.py` | Có quality engine. | Add live quote/candle consistency check: last candle close không lệch quote quá X pips. |
| `engines/*` | Nhiều engine strategy/AI. | Live path chỉ cho phép engine đã registered và có deterministic output; engine experimental phải ở paper/demo. |

### 4.3 `services/execution-service` — broker execution plane

| File/module | Hiện trạng | Hoàn thiện cần làm |
|---|---|---|
| `execution_engine.py` | Có gate verify, idempotency, mark submitting, timeout unknown. | Bắt buộc `mark_submitting_hook` và `enqueue_unknown_hook` trong live ngay từ constructor/preflight; persist broker result qua UoW. |
| `providers/base.py` | Contract khá đầy đủ. | Thêm `submit_order_with_client_id()` contract rõ; `verify_live_capability()` phải test client id echo. |
| `providers/ctrader.py` | Demo base + live mutation; adapter split. | Tách demo/live base, truyền client id thật, account id trong `AccountInfo`, raw response hash đầy đủ. |
| `providers/ctrader_live.py` | Wrapper lách demo-only guard. | Refactor như P0.2. |
| `unknown_order_reconciler.py` | Có logic classify filled/rejected/still_unknown. | Gắn vào API daemon và UoW persist. |
| `order_state_machine.py` | Có state machine. | DB transition phải enforce allowed transitions bằng service, không update status tự do. |
| `reconciliation_worker.py` | Runtime worker. | Kết hợp với daemon tránh double-processing qua lease + source-of-truth queue. |

### 4.4 `ai_trading_brain`

| File/module | Hiện trạng | Hoàn thiện cần làm |
|---|---|---|
| `brain_contracts.py` | Có input/output contracts. | Live output phải bắt buộc `cycle_id`, `policy_version`, `policy_hash`, `execution_intent`. |
| `brain_runtime.py` | Có cycle runtime. | Mỗi live cycle phải persist trước execution; nếu persist fail thì block. |
| `decision_engine.py` | Có decision engine. | Add deterministic replay payload: market snapshot hash, account snapshot hash, policy hash. |
| `governance.py` | Governance layer. | Add live approval gate: strategy/brain version must be approved. |
| `evolution_engine.py` | Có evolution. | Cấm auto-evolution đổi live strategy khi bot đang RUNNING; chỉ paper/demo hoặc approved rollout. |

### 4.5 Frontend/admin/operator

| File/module | Hiện trạng | Hoàn thiện cần làm |
|---|---|---|
| `apps/web/components/live/LiveReadinessPanel.tsx` | Có readiness panel. | Hiển thị từng hard gate: policy hash, quote freshness, spec hash, unknown orders, daemon status. |
| `DailyLockPanel.tsx` | Có panel daily lock. | Thêm nút manual reset có reason + approval + audit log. |
| `UnknownOrdersPanel.tsx` | Có unknown order panel. | Thêm action: reconcile now, close all, freeze bot, incident link. |
| `ExecutionReceiptDrawer.tsx` | Có receipt drawer. | Hiển thị raw_response_hash, broker_order_id, broker_deal_id, latency, account_id. |
| `apps/admin/app/broker-health/page.tsx` | Có broker health. | Hiển thị capability proof failed checks và tuổi proof. |

### 4.6 Legacy stack

| Module | Rủi ro | Hành động |
|---|---|---|
| `backend/` | Còn engine cũ, provider cũ, SQLite path. | Đổi thành `legacy/backend`, không build mặc định, thêm README cảnh báo. |
| `frontend/` | Streamlit legacy. | Đổi thành `legacy/frontend`. |
| root `docker-compose.yml` | Đang chạy legacy. | Thay bằng monorepo compose hoặc xóa khỏi release. |

---

## 5. Patch order đề xuất

### Phase P0 — Live Safety Closure

1. **Production compose isolation patch**  
   Root compose không còn legacy. Makefile target mặc định dùng monorepo stack.

2. **CTrader live provider contract patch**  
   Tách base/demo/live, bỏ mutation `kwargs["live"] = False`, verify client id echo.

3. **Policy hash hard gate patch**  
   Live thiếu real policy hash = BLOCK. Remove `policy_hash_unknown`.

4. **Quote/spec hash frozen context patch**  
   Bind quote id, quote timestamp, instrument spec hash vào gate và receipt.

5. **Atomic lifecycle UoW patch**  
   Reserve → submitting → broker result/unknown → receipt/projection trong một lifecycle service bắt buộc.

6. **Unknown order daemon broker-resolve patch**  
   Daemon gọi provider/reconciler thật trước retry/dead-letter.

7. **Broker-native risk v2 patch**  
   FX/crypto instrument metadata, account currency conversion, net/gross exposure.

8. **Live preflight full closure patch**  
   Kiểm required hooks, capability proof age, daemon running, no legacy stack, policy hash, daily state fresh.

### Phase P1 — Operator & Observability

1. Live operations dashboard hard gate matrix.  
2. Incident timeline + unknown order runbook.  
3. Prometheus metrics: order_unknown_count, reconciliation_deadletter_count, daily_lock_actions, broker_latency_ms.  
4. Audit log for manual reset/close all/reconcile now.

### Phase P2 — Strategy/AI Production Governance

1. Strategy version approval + canary mode.  
2. Brain replay artifact.  
3. Paper/demo shadow mode before live.  
4. Auto-evolution disabled in live unless approved rollout.

---

## 6. Dev-ready acceptance checklist

### Live start must fail if:

- provider mode is `stub`, `demo`, `unavailable`, `degraded`;
- `policy_hash` missing or equals `policy_hash_unknown`;
- `quote_id`/`quote_timestamp` missing or stale;
- `instrument_spec_hash` missing;
- daily state older than configured max age;
- unknown reconciliation queue has unresolved item;
- reconciliation daemon enabled but not running;
- root deployment uses legacy backend/frontend;
- broker does not support client-order-id echo lookup;
- required lifecycle hooks/UoW are missing;
- account equity from broker cannot be synced.

### A live order is allowed only when:

- brain cycle persisted with cycle id;
- approved policy hash is bound;
- account snapshot, quote, instrument spec and margin estimate are current;
- position size is recalculated using broker-native spec;
- `GateContextV1` hash matches frozen context;
- DB idempotency reservation exists before broker submit;
- SUBMITTING is persisted before broker call;
- broker receipt has `ACKED` + `FILLED/PARTIAL` + broker id/position/deal id + account id + raw response hash;
- if timeout/error occurs, unknown order is queued exactly once and bot cannot silently continue.

---

## 7. Kết luận cuối

`forex-main-7(1)` đã có rất nhiều thành phần đúng hướng để trở thành **live trading system thật**: runtime registry, gate, frozen context, broker contract, daily lock, order ledger, receipt, reconciliation, admin/web panels. Tuy nhiên bản này vẫn cần đóng các lỗ P0 trước khi chạy tiền thật, đặc biệt là:

1. **Cô lập legacy khỏi production**, vì root compose hiện vẫn chạy legacy.  
2. **Refactor cTrader live provider** để live/demo contract không bị mutation ngược.  
3. **Bắt buộc policy/quote/spec hash thật**, không fallback unknown.  
4. **Atomic order lifecycle end-to-end**, không phụ thuộc event/hook optional.  
5. **Unknown daemon phải query broker thật**, không chỉ retry/dead-letter.  
6. **Risk context v2 broker-native**, đủ Forex/Crypto/account currency/netting/hedging.

Sau khi hoàn thành P0 và chạy pass toàn bộ smoke/e2e trên broker demo/sandbox tối thiểu 2–4 tuần, mới nên cân nhắc live với vốn nhỏ, giới hạn lot thấp, daily loss/TP lock cứng và operator giám sát liên tục.
