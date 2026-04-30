# FOREX MAIN 14 — Deep File/Module/Engine Completion Audit

**Repo audited:** `/mnt/data/forex-main(14).zip`  
**Scope:** file inventory, module architecture, live trading readiness, broker/execution/risk/daily lock/reconciliation/API/frontend/CI.  
**Verdict:** **NEAR-LIVE, NOT YET SAFE FOR UNSUPERVISED REAL MONEY.** The repo has moved much closer to a production live trading system, especially around order ledger, outbox, frozen gate context, broker capability proof, reconciliation daemon, and daily lock. However, several modules still require hardening before real-money automation.

> Trading safety note: no code review can guarantee profit. A claim like 500% return on $500 is not a production acceptance criterion. The acceptance criterion must be capital preservation, deterministic execution, broker reconciliation, auditability, and fail-closed behavior.

---

## 1. Current architecture inventory

### 1.1 Production stack

| Area | Main files/folders | Current state |
|---|---|---|
| API backend | `apps/api/app/*` | Strongest part of repo. Has auth, bot service, live trading routes, policy, daily state, ledger, outbox, reconciliation queue, incidents. |
| Trading core | `services/trading-core/trading_core/*` | Contains runtime, pre-execution gate, frozen context, risk builder, broker-native risk modules and legacy-style strategy engines. |
| Execution service | `services/execution-service/execution_service/*` | Has provider abstraction, cTrader/MT5/Bybit/Paper providers, execution engine, order router, state machine, unknown reconciler. |
| Frontend app | `apps/web/*` | Has live control center, live orders, runtime control, broker connections, daily lock panel, receipt drawer, unknown orders panel. |
| Admin app | `apps/admin/*` | Has broker health, runtime, operations dashboard, users/workspaces. |
| Workers | `apps/api/app/workers/*` | Reconciliation daemon, reconciliation entrypoint, ledger integrity worker. Important production layer now exists. |
| DB migrations | `apps/api/alembic/versions/0001…0020` | Safety ledger, order state, receipts, policy approvals, daily lock, reconciliation queue, outbox, heartbeat. Good base. |
| Infra | `infra/docker/docker-compose.prod.yml`, `infra/monitoring/*`, `infra/nginx/*` | Production compose now avoids legacy root stack, includes API, web, reconciliation worker, integrity worker. |
| Legacy stack | `backend/*`, `frontend/*`, `ai_trading_brain/*` | Still present. Root compose disables it. Needs stronger archive/isolation policy. |

### 1.2 Major improvement versus previous audit patterns

- Root `docker-compose.yml` no longer starts legacy `backend` or `frontend`; it intentionally points to `infra/docker/docker-compose.dev.yml` and `infra/docker/docker-compose.prod.yml`.
- `infra/docker/docker-compose.prod.yml` includes dedicated `reconciliation-worker` and `integrity-worker`.
- Execution path now has `SubmitOutboxService`, `OrderLedgerService`, `BrokerExecutionReceipt`, `OrderStateTransition`, `ReconciliationQueueItem`, `WorkerHeartbeat`.
- `ExecutionEngine.place_order()` requires `ExecutionCommand` in live mode, checks idempotency reservation, validates frozen gate hash, validates frozen bindings, marks `SUBMITTING` before broker send, records outbox phases, and enqueues unknown after timeout.
- `LiveStartPreflight` is now fail-closed on broker health, broker capability proof, reconciliation daemon health, active policy, broker equity sync, daily lock, unknown orders, and critical incidents.
- `PreExecutionGate` now enforces `gate_context_v2`, policy hash, policy version, instrument spec hash, quote id/timestamp, broker account snapshot hash, broker snapshot hash, risk context hash, unresolved unknown orders, quote age, SL, margin, exposure, daily TP/loss.

---

## 2. Final verdict by module

| Module | Production score | Verdict |
|---|---:|---|
| DB schema/migrations | 8.3/10 | Good safety schema; needs stricter DB constraints/indexes and migration CI evidence. |
| API live trading control plane | 8.0/10 | Good endpoints; manual resolution exists; needs more operator actions and RBAC granularity. |
| Bot runtime wiring | 7.4/10 | Strong hooks; still has stub fallback path for non-live; live guard improved. Needs better failure surfacing. |
| Execution engine | 8.2/10 | Good atomic submit design; needs transactional outbox/ledger consistency and provider-specific receipt proof. |
| Broker providers | 6.6/10 | Interface is good; cTrader live still depends on underlying engine capabilities. Real provider contract must be proven with sandbox/live smoke tests. |
| Risk engine | 7.5/10 | Broker-native margin required in live; exposure still approximation-heavy and needs multi-currency/portfolio normalization. |
| Frozen gate context | 8.0/10 | Strong V2 binding; needs fully canonical hash contract and signed/persisted context snapshot. |
| Daily TP/loss lock | 7.8/10 | Controller/orchestrator exists; needs exactly-once close-all postcondition and dashboard manual reset workflow. |
| Unknown reconciliation | 7.8/10 | Daemon + queue + lease exist; needs broker-native lookup proof, dead-letter UX, and separate critical stop flow. |
| Frontend/operator UI | 7.0/10 | Live panels exist; needs production-grade incident workflow, acknowledgements, audit trail, and Vietnamese UI consistency. |
| CI/verification | 7.6/10 | Many verify scripts exist; needs one master release gate and broker sandbox integration tests. |
| Legacy isolation | 7.2/10 | Better than before; legacy still present and can confuse imports/devs. Needs archive boundary. |

**Overall:** 7.6/10.  
**Status:** ready for paper/demo, limited supervised broker sandbox, not ready for unattended live capital.

---

## 3. File-by-file/module-by-module findings

## 3.1 Root and infra

### `docker-compose.yml`

**Current:** root compose intentionally has `services: {}` and warns to use infra compose. This is good because it prevents accidental launch of legacy backend/frontend.

**Remaining gap:** a developer can still run `backend/Dockerfile` or `frontend/Dockerfile` manually.

**Patch:**
- Add `backend/README_DEPRECATED.md` hard warning plus runtime exit in `backend/main.py` when `APP_ENV=production`.
- Add CI check that no production compose, Makefile, GitHub workflow, or Dockerfile references `./backend` or `./frontend`.
- Move old `backend` and `frontend` under `legacy/` or `archive/` after compatibility review.

### `infra/docker/docker-compose.prod.yml`

**Current:** includes `postgres`, `redis`, `api`, `reconciliation-worker`, `integrity-worker`, `web`, `nginx`.

**Gap:** production compose has no migration job, no healthcheck gating, no worker dependency on DB migrations, no monitoring/alerting services in prod compose.

**Patch:**
- Add one-shot `migrate` service before API/worker starts.
- Add `healthcheck` to API, Postgres, Redis, reconciliation worker heartbeat, integrity worker heartbeat.
- Add `prometheus`, `grafana`, `loki` or connect to external monitoring.
- Add `restart: always` is present, but also add `stop_grace_period` for workers so leases can release cleanly.

---

## 3.2 API backend: `apps/api/app`

### `apps/api/app/main.py`

**Current:** production API entrypoint with routers and registry. Good control-plane entry.

**Patch needed:**
- Add `/health/live`, `/health/ready`, `/health/deep`.
- `/health/deep` must verify DB migration head, Redis, runtime registry, reconciliation heartbeat, integrity heartbeat, broker connection decrypt test only when needed.
- Expose Prometheus `/metrics` with counters for order states, unknown queue, daily locks, broker failures.

### `apps/api/app/models/__init__.py`

**Current:** comprehensive SQLAlchemy model file. Important tables exist:
- `Order`
- `Trade`
- `TradingDecisionLedger`
- `PreExecutionGateEvent`
- `OrderIdempotencyReservation`
- `DailyTradingState`
- `BrokerOrderAttempt`
- `SubmitOutbox`
- `WorkerHeartbeat`
- `OrderStateTransition`
- `BrokerExecutionReceipt`
- `BrokerAccountSnapshot`
- `BrokerReconciliationRun`
- `ReconciliationQueueItem`
- `TradingIncident`
- `DailyLockAction`

**Gaps:**
- Several IDs use `String(64)` even though bot IDs are UUID strings; okay but should be standardized.
- `BrokerExecutionReceipt` lacks explicit unique constraint on `(bot_instance_id, idempotency_key, broker_order_id, broker_deal_id)`.
- `OrderStateTransition` unique key may block legitimate repeated same-state broker events if payload differs. Good for idempotency, but you may need `event_hash`.
- `Order` still has both `status` and `current_state`; source-of-truth semantics must be documented and enforced.

**Patch:**
- Add `event_hash` to `order_state_transitions`.
- Add `source_of_truth` enum fields: `orders.current_state` projected, `broker_order_attempts.current_state` authoritative attempt state, `broker_execution_receipts` immutable evidence.
- Add indexes:
  - `orders(bot_instance_id, current_state)`
  - `broker_order_attempts(bot_instance_id, current_state)`
  - `reconciliation_queue_items(status, next_retry_at, leased_until)`
  - `trading_incidents(bot_instance_id, status, severity)`
- Add DB check constraints for allowed state values.

### `apps/api/app/services/live_start_preflight.py`

**Current:** strong fail-closed preflight. It checks provider readiness, provider contract, capability proof, reconciliation daemon health, approved policy, daily broker equity sync, daily lock, unresolved unknown orders, critical incidents.

**Gaps:**
- Does not verify all workers are on the same git SHA/runtime version.
- Does not assert DB migration head before live start.
- Does not verify clock sync between API host and broker beyond provider proof.
- Does not verify notification channels before live start.

**Patch:**
- Add `migration_head_ok`, `runtime_version_match`, `notification_channel_ok`, `broker_clock_drift_ok`, `worker_version_match` to checks.
- Persist each preflight run to `live_start_preflight_runs` table with pass/fail details.
- Require operator acknowledgement for first live start per broker account.

### `apps/api/app/services/live_readiness_guard.py`

**Current:** checks provider is connected, mode not stub/paper, health, equity, capability proof, live provider protocol, spec/quote/margin.

**Critical finding:** default `BrokerProvider.verify_live_capability()` treats exceptions from `get_order_by_client_id`, `get_executions_by_client_id`, and `close_all_positions` as capability present. This is risky because a provider could raise due to auth/permission and still pass those capability flags.

**Patch:**
- In live mode, only count lookup/close capability as passed if method returns a valid typed response or a known `not_found`/empty result code from broker.
- Do not treat generic exceptions as capability present.
- Add provider-specific capability proofs, not generic proof only.

### `apps/api/app/services/bot_service.py`

**Current:** runtime hooks are extensive and live mode blocks ImportError fallback. Stub runtime remains only for non-live environments. `assert_runtime_live_guard` stops bad live runtimes.

**Gaps:**
- `llm_mode=stub` is allowed in readiness. For live trading, LLM should not be allowed to directly trade unless deterministic strategy gate owns final execution. If LLM is only advisory, mark it explicitly.
- The `_register_stub` helper still hides errors in non-live; acceptable for tests, but can mask bad staging.
- Runtime failure reason should be persisted into `TradingIncident`, not only raised/logged.

**Patch:**
- Add `live_ai_mode_policy`: `disabled | advisory_only | deterministic_only`; block `llm_mode=stub` if strategy expects AI signal.
- Persist runtime creation failure as incident.
- Add per-bot `runtime_config_hash` persisted and shown in UI.

### `apps/api/app/services/submit_outbox_service.py`

**Current:** tracks phase by `(bot_instance_id, idempotency_key)` with phase payload.

**Gaps:**
- It overwrites phase history; you lose full timeline from `BROKER_SEND_STARTED` to `BROKER_SEND_RETURNED` to `UNKNOWN_AFTER_SEND`.
- A single current row is useful, but immutable event history is needed for incident audit.

**Patch:**
- Keep `submit_outbox` as current projection.
- Add `submit_outbox_events` append-only table with `phase`, `request_hash`, `payload_hash`, `created_at`.
- Never overwrite broker-send history in live mode.

### `apps/api/app/services/order_ledger_service.py`

**Current:** central ledger service exists. This is the correct direction.

**Patch:**
- Enforce state transition matrix in one place only.
- Add `record_lifecycle_event(..., expected_from_state=...)` optimistic concurrency.
- Add exactly-once idempotency via `event_hash`.
- Add invariant checker: every `FILLED` order must have a receipt; every `UNKNOWN` must have queue item; every queue dead-letter must have critical incident and daily lock.

### `apps/api/app/services/daily_lock_runtime_controller.py` and `daily_lock_orchestrator.py`

**Current:** daily lock engine and orchestrator exist; close-all fallback is referenced and incomplete close can raise.

**Gaps:**
- Need stronger postcondition: after close-all, broker open positions for bot/symbol must be zero or incident created.
- Need manual reset API with two-person approval for live accounts.
- Need portfolio-level lock, not only bot daily lock.

**Patch:**
- Add `daily_lock_actions` state machine: `pending -> running -> verifying -> completed | failed | compensating`.
- Add broker-side close-all receipt collection.
- Add `manual_reset_daily_lock` endpoint requiring admin + reason + broker snapshot proof.
- Add `portfolio_daily_state` for multi-bot accounts.

### `apps/api/app/workers/reconciliation_daemon.py`

**Current:** strong daemon design: queue leasing, broker provider reconstruction if runtime is down, source-of-truth broker lookup, ledger persist before queue resolve, escalation to critical incident and daily lock.

**Gaps:**
- `_attempt_broker_reconcile()` creates provider from DB but does not run full live readiness/capability proof before query.
- It returns `False` for provider missing, which can eventually escalate, but root cause should be explicit.
- It does not separate “broker unreachable” from “order not found” for operator diagnosis.

**Patch:**
- Add queue `last_resolution_code`: `provider_missing`, `provider_auth_failed`, `lookup_failed`, `not_found`, `filled`, `rejected`, `ambiguous`.
- Run a lightweight provider readiness check before lookup.
- Persist each reconciliation attempt into `reconciliation_attempt_events`.
- Dead-letter should include exact broker query proof and last raw response hash.

---

## 3.3 Execution service

### `services/execution-service/execution_service/providers/base.py`

**Current:** defines `OrderRequest`, `OrderResult`, `ExecutionReceipt`, `AccountInfo`, `BrokerCapabilityProof`, `PreExecutionContext`, `ExecutionCommand`, `BrokerProvider`, and `LiveBrokerProviderProtocol`.

**Gaps:**
- `ExecutionReceipt` exists but `ExecutionEngine` still returns `OrderResult`; receipt contract is partly embedded in `OrderResult`.
- `BrokerCapabilityProof` generic proof is too permissive on exceptions.

**Patch:**
- Make live `place_order()` return `ExecutionReceipt` internally, then map to `OrderResult` for compatibility.
- Add strict `ReceiptContract.validate_live(result)` with provider-specific required fields.
- Modify capability proof logic: generic exception = failed check, not passed.

### `services/execution-service/execution_service/execution_engine.py`

**Current:** this is one of the strongest files. Live mode now requires:
- provider supports client order id
- brain cycle id
- idempotency key
- pre-execution context
- idempotency reservation verifier
- gate context hash
- frozen gate context
- frozen context binding validation
- pre-execution gate ALLOW
- SUBMITTING hook before broker send
- outbox phase before and after send
- timeout -> unknown queue
- live receipt contract enforcement

**Remaining P0 gaps:**
- `mark_submitting_hook` and `mark_submit_phase_hook` are separate DB commits. A crash between them may still be okay, but consistency is not fully atomic.
- Broker send and DB outbox cannot be one DB transaction because broker is external; this is normal. But the system needs a deterministic recovery scanner for `SUBMITTING`/`BROKER_SEND_STARTED` older than threshold.
- `_enforce_live_receipt_contract()` requires `ACKED + FILLED/PARTIAL`; some real brokers submit market orders as accepted/pending before fill. If the strategy supports only immediate market fills, document and enforce it. If pending is allowed, model pending lifecycle.

**Patch:**
- Add recovery scanner: any `submit_outbox.phase in ('SUBMITTING','BROKER_SEND_STARTED')` older than 15s -> enqueue reconciliation.
- Add `broker_submit_policy`: `market_ioc_only` vs `pending_allowed`.
- If `pending_allowed`, receipt contract should accept `ACKED/PENDING` but must create order state `BROKER_ACKED_PENDING`, not `FILLED`.
- Persist `request_hash` into `BrokerOrderAttempt` and compare during reconciliation.

### `services/execution-service/execution_service/providers/ctrader.py`

**Current:** cTrader provider is split into demo/live wrapper, delegates to execution and market data adapters, implements live-required methods like `get_instrument_spec`, `estimate_margin`, `get_order_by_client_id`, `get_executions_by_client_id`, `close_all_positions`, `get_server_time`, `get_quote`.

**Critical risk:** live methods are only as real as the underlying `_provider` methods. If the underlying cTrader engine lacks `get_instrument_spec`, `estimate_margin`, direct order lookup, direct execution lookup, live will fail closed. Good for safety, but not yet proven as production-ready.

**Patch:**
- Build a real cTrader Open API provider contract layer:
  - symbol id resolution cache
  - account authorization proof
  - quote stream with `quote_id`, timestamp, bid/ask
  - symbol spec mapping: pip size, tick size, contract size, min volume, volume step, commission model, margin rate/leverage
  - margin estimate using broker endpoint or exact replicated broker formula
  - order submit with client id/comment roundtrip
  - order lookup by client id/comment
  - deal/execution lookup by client id/comment
  - close position and close all with postcondition verification
- Add `tests/live_sandbox/test_ctrader_contract.py` gated by env vars.

### `services/execution-service/execution_service/providers/mt5_live.py`, `bybit_live.py`

**Current:** live wrappers exist.

**Patch:**
- Each live provider must pass the same `LiveBrokerProviderProtocol` with sandbox/live proof.
- For Bybit, use `orderLinkId` as true idempotency transport.
- For MT5, define how client id is stored: magic/comment plus collision-proof format.

### `services/execution-service/execution_service/unknown_order_reconciler.py`

**Current:** resolves unknown by order lookup and executions lookup; no DB writes by itself.

**Gaps:**
- No ambiguous outcome state. Some brokers can return both order and partial executions.
- No normalization layer per broker.

**Patch:**
- Add `BrokerOrderTruth` normalized dataclass.
- Add outcomes: `filled`, `partial`, `pending`, `rejected`, `cancelled`, `not_found`, `ambiguous`, `lookup_error`.
- Handle partial fills as a first-class state.

---

## 3.4 Trading core

### `services/trading-core/trading_core/runtime/pre_execution_gate.py`

**Current:** strong hard gate. It blocks kill switch, portfolio kill, daily lock, paused orders, broker not connected, provider not live capable, unapproved policy, invalid gate schema, missing hashes, unknown orders, stale quote, missing SL, news/session/clock/market data, daily loss, daily TP, margin, max risk amount, lot limits, exposure, slippage, spread, max positions, duplicate idempotency, confidence, RR.

**Patch:**
- Split gate into deterministic sub-gates with structured output:
  - `BrokerGate`
  - `MarketDataGate`
  - `PolicyGate`
  - `RiskGate`
  - `DailyLockGate`
  - `PortfolioGate`
  - `SignalQualityGate`
- Each sub-gate returns machine-readable `code`, `severity`, `operator_action`, `lock_scope`.
- Persist the exact gate input hash and gate result hash.

### `services/trading-core/trading_core/runtime/frozen_context_contract.py`

**Current:** validates V2 gate context bindings against execution request: bot, idempotency, cycle, account, broker, policy, schema, hashes, symbol, side, volume, account, order type, price deviation.

**Gap:** frozen context is validated but not clearly signed or persisted as immutable evidence before broker send.

**Patch:**
- Persist `FrozenGateContext` row with canonical JSON, hash, `created_at`, `runtime_version`, `policy_version_id`, `broker_snapshot_hash`, `risk_context_hash`.
- Add HMAC signature using server secret for tamper evidence.
- Include `approved_volume`, `approved_price`, `approved_sl`, `approved_tp`, `max_slippage_pips`, `max_price_deviation_bps`.

### `services/trading-core/trading_core/risk/risk_context_builder.py`

**Current:** requires real instrument spec and broker-native margin estimate in live mode. Calculates account/symbol exposure and SL loss.

**Gaps:**
- Exposure is not currency-normalized for non-account currency pairs.
- Correlated USD exposure is coarse: any symbol containing USD.
- Pip value may be wrong for cross pairs and crypto instruments.

**Patch:**
- Add `CurrencyConversionService` using broker quote snapshots.
- Normalize notional/exposure to account currency.
- Add correlation buckets: USD, EUR, JPY, GBP, XAU, BTC/ETH, custom risk groups.
- Add instrument class: `forex`, `metal`, `crypto`, `index`, `commodity`.
- Add broker commission/slippage model into `max_loss_amount_if_sl_hit`.

### `services/trading-core/trading_core/runtime/bot_runtime.py`

**Current:** large orchestration runtime. Has live-mode no legacy queue fallback comments, provider mode guard, risk/gate logic, hooks.

**Patch:**
- Split into smaller units:
  - `SignalCycleRunner`
  - `RiskContextAssembler`
  - `GateContextFreezer`
  - `ExecutionCommandBuilder`
  - `RuntimeSnapshotBuilder`
- Keep public behavior unchanged, but reduce single-file risk.
- Add trace id/cycle id to every log/event.

### `services/trading-core/trading_core/engines/*`

**Current:** many AI/strategy engines exist: `auto_pilot`, `decision_engine`, `entry_logic`, `risk_manager`, `session_manager`, `signal_coordinator`, `wave_detector`, `performance_tracker`, `meta_learning_engine`, etc.

**Risk:** many engines are legacy-style and may contain fallback/heuristic logic. They are useful for signal generation, but they must never bypass the production gate/execution path.

**Patch:**
- Define engine contract: engines output `SignalIntent` only.
- Engines cannot place orders, change risk policy, or bypass pre-execution gate.
- Add `StrategySignalContract` validation: symbol, side, confidence, entry, SL, TP, RR, source_engine, strategy_version.
- Add experiment stage policy: unvalidated strategy can only run in backtest/paper/demo.

---

## 3.5 Frontend/operator UI

### `apps/web/app/(app)/live-control-center/page.tsx`

**Current:** dedicated live center exists.

**Patch:**
- Add one screen showing: `Can this bot trade now? YES/NO` with exact blockers.
- Display: broker mode, account id, equity, daily TP/loss, unknown queue, last reconciliation, policy version, frozen context hash, runtime version.
- Add red fail-closed banner if any `critical` incident is open.

### `apps/web/components/live/*`

Current components:
- `DailyLockPanel.tsx`
- `ExecutionReceiptDrawer.tsx`
- `LiveReadinessPanel.tsx`
- `ReconciliationTimeline.tsx`
- `UnknownOrdersPanel.tsx`

**Patch:**
- Add `ManualReconciliationModal` requiring broker proof upload/paste.
- Add `DailyLockResetModal` requiring admin reason and broker snapshot.
- Add `OrderLifecycleTimeline` combining decision -> gate -> attempt -> outbox -> receipt -> order projection -> trade.
- Vietnamese UI consistency: all visible text should default to Vietnamese unless product requires English.

---

## 3.6 AI trading brain layer

### `ai_trading_brain/*`

**Current:** standalone brain runtime and governance layer. Good for experimentation, but potentially parallel to production `services/trading-core`.

**Gap:** two brain systems can create confusion: `ai_trading_brain` and `trading_core/engines`.

**Patch:**
- Mark `ai_trading_brain` as research/advisory unless wired through `BotRuntime`.
- Add import guard: production API cannot import `ai_trading_brain` directly except through an approved adapter.
- Convert brain output to `SignalIntent`, not execution command.

---

## 4. P0 completion plan — must finish before real live money

### P0.1 — Strict broker capability proof

**Files:**
- `execution_service/providers/base.py`
- `execution_service/providers/ctrader.py`
- `execution_service/providers/mt5_live.py`
- `execution_service/providers/bybit_live.py`
- `apps/api/app/services/live_readiness_guard.py`

**Required changes:**
1. Generic exception in capability proof = fail, not pass.
2. Provider-specific proof classes.
3. Sandbox integration tests for each broker.
4. Capability proof persisted with raw hashes.

**Acceptance:** live start impossible unless provider proves quote, server time, symbol spec, margin estimate, client id roundtrip, order lookup, execution lookup, close-all.

### P0.2 — Atomic order lifecycle recovery scanner

**Files:**
- `execution_engine.py`
- `submit_outbox_service.py`
- `reconciliation_daemon.py`
- new `apps/api/app/workers/submit_outbox_recovery_worker.py`

**Required changes:**
1. Append-only submit outbox event history.
2. Scanner finds stale `SUBMITTING`/`BROKER_SEND_STARTED`.
3. Enqueue unknown reconciliation automatically.
4. Critical incident if scanner itself unhealthy.

**Acceptance:** crash after DB mark but before/after broker send always becomes reconciled or dead-lettered with lock.

### P0.3 — Broker-native risk context v3

**Files:**
- `risk_context_builder.py`
- `broker_native_risk_context.py`
- `instrument_spec.py`
- `broker_snapshot.py`

**Required changes:**
1. Normalize exposure to account currency.
2. Multi-asset instrument class support.
3. Broker commission/slippage included in worst-case loss.
4. Correlation buckets configurable by policy.

**Acceptance:** no live order can be sized using fallback spec, stale quote, or non-broker margin estimate.

### P0.4 — Frozen Gate Context persistence/signature

**Files:**
- `frozen_context_contract.py`
- `pre_execution_gate.py`
- `models/__init__.py`
- new migration `0021_frozen_gate_contexts.py`

**Required changes:**
1. Store canonical context JSON before execution.
2. Store context hash + HMAC signature.
3. Execution command must reference stored frozen context id.
4. Reconciliation and receipt must link back to context id.

**Acceptance:** every broker attempt has immutable evidence of why it was allowed.

### P0.5 — Daily lock exactly-once close-all

**Files:**
- `daily_lock_runtime_controller.py`
- `daily_lock_orchestrator.py`
- `live_trading.py`
- `DailyLockPanel.tsx`

**Required changes:**
1. Postcondition after close-all: broker positions = zero for locked scope.
2. If not zero -> critical incident + operator required.
3. Manual reset endpoint with audit and RBAC.
4. Portfolio-level daily lock.

**Acceptance:** hitting Daily TP or daily loss cannot silently allow another order.

### P0.6 — Unknown reconciliation production proof

**Files:**
- `unknown_order_reconciler.py`
- `reconciliation_daemon.py`
- `reconciliation_queue_service.py`
- `UnknownOrdersPanel.tsx`

**Required changes:**
1. Normalize broker truth into `BrokerOrderTruth`.
2. Distinguish `not_found`, `lookup_failed`, `ambiguous`, `partial`, `pending`.
3. Persist every broker lookup attempt.
4. Manual resolution requires broker proof.

**Acceptance:** no unknown order remains invisible; unresolved unknown always blocks new live orders.

### P0.7 — Production release gate

**Files:**
- `.github/workflows/release.yml`
- `.github/scripts/*`
- `scripts/ci/*`

**Required checks:**
1. Alembic single head + upgrade/downgrade smoke.
2. No legacy import boundary breach.
3. No stub/paper provider in live path.
4. Production compose has no legacy stack.
5. Runtime snapshot contract.
6. Broker gate wiring.
7. Market data quality scenarios.
8. Outbox recovery scenario.
9. Daily lock close-all scenario.
10. Unknown reconciliation scenario.

**Acceptance:** release blocked unless all live safety checks pass.

---

## 5. P1 completion plan — production operations

1. **Observability:** Prometheus counters for `orders_by_state`, `unknown_queue_age`, `daily_lock_active`, `broker_submit_latency`, `broker_error_rate`.
2. **Incident workflow:** acknowledge, assign, resolve, postmortem seed.
3. **RBAC:** admin/operator/viewer; only admin can manual reconcile/reset daily lock.
4. **Secrets:** rotate broker credentials, audit decrypt access, never show tokens in frontend.
5. **Audit export:** one-click export for a trade: signal, decision, gate, frozen context, broker receipt, reconciliation events.
6. **Backtest-to-live parity:** ensure strategy configs that pass backtest cannot auto-run live without approved policy and demo soak period.
7. **Canary mode:** live mode with min lot only, daily TP/loss ultra conservative, operator approval per order.

---

## 6. P2 completion plan — scale and SaaS readiness

1. Multi-tenant billing enforcement by workspace plan.
2. Broker connection vault with KMS.
3. Strategy marketplace/version approval.
4. Portfolio-level risk across bots and brokers.
5. Broker adapters for cTrader, MT5, Bybit with same certification suite.
6. Disaster recovery: DB backups, restore drills, incident timeline replay.
7. Regulatory/audit docs: risk disclosure, operator controls, no profit guarantee claims.

---

## 7. Recommended next patch order

1. **BUILD P0 STRICT BROKER CAPABILITY PROOF PATCH**
2. **BUILD P0 SUBMIT OUTBOX RECOVERY WORKER PATCH**
3. **BUILD P0 FROZEN GATE CONTEXT PERSISTENCE PATCH**
4. **BUILD P0 BROKER-NATIVE RISK V3 PATCH**
5. **BUILD P0 DAILY LOCK EXACTLY-ONCE CLOSE-ALL PATCH**
6. **BUILD P0 UNKNOWN RECONCILIATION TRUTH MODEL PATCH**
7. **BUILD P0 MASTER RELEASE GATE PATCH**
8. **BUILD P1 OPERATOR INCIDENT WORKFLOW PATCH**
9. **BUILD P1 OBSERVABILITY + PROMETHEUS PATCH**
10. **BUILD P1 FRONTEND LIVE COMMAND CENTER PATCH**

---

## 8. Final production readiness statement

This codebase is no longer a simple prototype. It has many of the right production primitives: live preflight, broker capability proof, frozen context, order ledger, execution receipts, reconciliation queue, daily lock, worker heartbeats, live dashboard components, and CI guard scripts.

However, the highest-risk boundary remains the same: **the broker boundary**. Until the live broker provider proves quote/spec/margin/idempotency/order lookup/execution lookup/close-all against real broker sandbox/live accounts, the system must remain in paper/demo or supervised canary only.

**Final decision:**

```text
RUN_MODE_ALLOWED:
  backtest: YES
  paper: YES
  demo: YES
  live_canary_supervised: AFTER P0 patches
  live_unattended_real_money: NO
```
