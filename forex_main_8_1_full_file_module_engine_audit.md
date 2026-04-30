# FOREX MAIN 8(1) — FULL FILE / MODULE / ENGINE COMPLETION AUDIT

**Repo:** `/mnt/data/forex-main-8(1).zip`  
**Audit mode:** file-by-file / module-by-module / engine-by-engine review for real live trading readiness.  
**Scope inspected:** 369 files including `apps/api`, `apps/web`, `services/trading-core`, `services/execution-service`, `ai_trading_brain`, infra, tests, CI scripts, legacy `backend/` and `frontend/`.

---

## 0. Executive verdict

**Status:** `NEAR-LIVE / NOT YET UNSUPERVISED REAL-MONEY READY`

The repo is materially stronger than earlier iterations. It now contains:

- explicit root compose isolation from legacy stack;
- API runtime registry boot;
- live preflight checks;
- broker capability proof concept;
- frozen gate context and hash binding;
- broker-native risk context builder;
- order ledger tables, projections, receipts, transitions;
- unknown order queue and daemon;
- daily TP/loss lock controller;
- admin/live operator UI pages;
- CI scripts for no-stub/no-legacy/live boundary checks.

However, the live trading safety loop is **not fully closed** yet. The system can be promoted to supervised demo/live pilot only after fixing the P0 items below.

---

## 1. Top P0 blockers before running real money

### P0.1 — BrokerCapabilityProof is too weak

**Files:**

- `services/execution-service/execution_service/providers/base.py`
- `apps/api/app/services/live_readiness_guard.py`
- `apps/api/app/services/live_start_preflight.py`
- `services/execution-service/execution_service/providers/ctrader.py`

**Current finding:**

`BrokerCapabilityProof.all_required_passed` does **not require**:

- `margin_estimate_valid`
- `execution_lookup_supported`

This is dangerous because unknown-order recovery and broker-native risk depend on both. Also default proof marks lookup support by `getattr(...)` existing, but base class methods exist and may still raise `NotImplementedError`. That means a provider can pass structure checks without proving an actual broker roundtrip.

**Required completion:**

- Add `margin_estimate_valid` and `execution_lookup_supported` to `all_required_passed`.
- Replace `getattr exists` with actual callable dry-run checks:
  - `get_instrument_spec(symbol)` must return normalized spec with `pip_size`, `contract_size`, `min_volume`, `volume_step`.
  - `estimate_margin(symbol, side, volume, price)` must return `> 0` in live.
  - `get_quote(symbol)` must include `bid`, `ask`, `timestamp`, `quote_id`, and freshness validation.
  - order/execution lookup must either perform provider-specific capability proof or return a signed proof object that the API endpoint supports client-order-id lookup.
- Store capability proof result in DB with timestamp, provider, account, symbol, and proof hash.

---

### P0.2 — cTrader live provider still wraps an engine that may not be true Open API execution

**Files:**

- `services/execution-service/execution_service/providers/ctrader.py`
- `services/execution-service/execution_service/providers/ctrader_live.py`
- `services/execution-service/execution_service/providers/ctrader_execution_adapter.py`
- `services/trading-core/trading_core/engines/ctrader_provider.py`

**Current finding:**

`CTraderLiveProvider` hard-pins `live=True`, which is good. But `CTraderProvider.connect()` imports `trading_core.engines.ctrader_provider.CTraderDataProvider`. That engine appears to be a data/provider abstraction and execution capability is inferred by checking whether it has methods such as `place_market_order`, `close_position`, `get_positions`, etc.

The adapter design is clean, but live trading needs a real broker SDK adapter, not “does the engine object happen to expose methods?”.

**Required completion:**

Create a true live adapter split:

```text
services/execution-service/execution_service/providers/ctrader_openapi_live.py
services/execution-service/execution_service/providers/ctrader_openapi_auth.py
services/execution-service/execution_service/providers/ctrader_openapi_mapper.py
services/execution-service/execution_service/providers/ctrader_openapi_reconcile.py
```

Must implement:

- OAuth/token refresh lifecycle;
- account authorization and account ID match;
- symbol metadata from broker;
- quote subscription with freshness;
- `place_market_order(client_order_id=...)` with raw response capture;
- order/deal/position lookup by `client_order_id/comment/clientMsgId`;
- `close_all_positions` with post-close verification;
- no fallback to engine/data provider in live mode.

---

### P0.3 — Order lifecycle is improved, but transition source-of-truth still needs stricter atomicity

**Files:**

- `apps/api/app/services/order_ledger_service.py`
- `apps/api/app/services/safety_ledger.py`
- `apps/api/app/services/order_projection_service.py`
- `apps/api/app/models/__init__.py`
- `services/execution-service/execution_service/execution_engine.py`

**Current finding:**

There is a solid ledger model:

- `broker_order_attempts`
- `order_state_transitions`
- `broker_execution_receipts`
- `orders` projection
- `reconciliation_queue_items`

But `ExecutionEngine` calls broker after `mark_submitting_hook`, and API lifecycle persistence is split across hooks. This is close, but production needs one explicit order lifecycle transaction boundary and transition validator called on every state change.

**Required completion:**

Implement a strict lifecycle state machine:

```text
INTENT_RESERVED
→ PENDING_SUBMIT
→ SUBMIT_REQUESTED
→ ACKED | REJECTED | UNKNOWN
→ FILLED | PARTIAL | CANCELLED | EXPIRED | NEEDS_OPERATOR
```

Rules:

- illegal transition = hard fail + incident;
- duplicate idempotency = return existing projection, never resubmit;
- `SUBMIT_REQUESTED` older than N seconds = auto enqueue unknown;
- `UNKNOWN` cannot be manually set to `FILLED` without broker receipt proof;
- all updates to attempt, receipt, transition and projection must occur in one service method.

Add tests:

- crash after submit before receipt;
- duplicate retry with same idempotency key;
- broker timeout then later filled;
- broker timeout then not found;
- partial fill;
- duplicate receipt idempotency.

---

### P0.4 — Unknown order daemon exists, but broker resolution result is not persisted strongly enough

**Files:**

- `apps/api/app/workers/reconciliation_daemon.py`
- `apps/api/app/services/reconciliation_queue_service.py`
- `services/execution-service/execution_service/unknown_order_reconciler.py`
- `services/execution-service/execution_service/reconciliation_worker.py`

**Current finding:**

The daemon can poll `reconciliation_queue_items`, retry after 30 seconds, escalate after 5 minutes or 3 attempts, create incident, and daily-lock bot. Good.

But `_attempt_broker_reconcile()` returns only `True/False`. It does not guarantee that the broker resolution has been persisted into:

- `broker_execution_receipts`
- `broker_order_attempts`
- `order_state_transitions`
- `orders` projection

unless the reconciler callback is properly wired. In daemon mode, `UnknownOrderReconciler` is constructed without `on_resolved`, so the queue may become resolved without full ledger mutation.

**Required completion:**

- Daemon must call an API-side `OrderLifecycleResolver` that persists final broker truth before `mark_resolved`.
- Require reconciler result object with:
  - outcome;
  - broker_order_id;
  - broker_position_id;
  - broker_deal_id;
  - raw response hash;
  - resolved_at;
  - final transition.
- Queue `resolved` must only happen after ledger/projection commit succeeds.
- If broker says found but persistence fails: queue stays retry/in_progress, incident severity warning/critical depending on age.

---

### P0.5 — Live risk context depends on provider spec/margin, but quote/spec hashes are not broker-proofed end-to-end

**Files:**

- `services/trading-core/trading_core/risk/risk_context_builder.py`
- `services/trading-core/trading_core/risk/broker_native_risk_context.py`
- `services/trading-core/trading_core/risk/instrument_spec.py`
- `services/trading-core/trading_core/runtime/pre_execution_gate.py`
- `services/trading-core/trading_core/runtime/frozen_context_contract.py`
- `services/trading-core/trading_core/runtime/bot_runtime.py`

**Current finding:**

Live mode correctly blocks fallback instrument spec and missing broker margin estimate. Gate context V1 includes symbol, side, account, policy, quote, spec hash, starting equity, slippage, approved volume. This is the right direction.

Remaining risk: hashes are only as trustworthy as their source. If quote/spec are derived from fallback or stale adapter, gate can hash weak data.

**Required completion:**

- Introduce `BrokerQuoteSnapshot` and `BrokerInstrumentSpecSnapshot` dataclasses.
- Require broker-native snapshot fields:
  - `symbol`, `bid`, `ask`, `timestamp`, `quote_id`, `source`, `latency_ms`;
  - `pip_size`, `tick_size`, `contract_size`, `min_volume`, `max_volume`, `volume_step`, `margin_rate/leverage`, `currency_profit`, `currency_margin`.
- Compute `quote_hash` and `instrument_spec_hash` only from normalized broker-native snapshots.
- In live mode, reject:
  - no quote id;
  - stale quote;
  - missing volume step;
  - estimated pip value without conversion rate;
  - margin estimate <= 0;
  - stop-loss missing.

---

### P0.6 — Daily TP/loss lock exists, but lock-action semantics need final hardening

**Files:**

- `apps/api/app/services/daily_profit_lock_engine.py`
- `apps/api/app/services/daily_lock_runtime_controller.py`
- `apps/api/app/services/daily_trading_state.py`
- `services/trading-core/trading_core/risk/daily_profit_policy.py`
- `services/trading-core/trading_core/runtime/pre_execution_gate.py`

**Current finding:**

The repo has daily state, daily lock action rows, and runtime controller. It supports actions such as `stop_new_orders`, `close_all_and_stop`, `reduce_risk_only`. It also verifies remaining positions after close.

Remaining gap: failure policy must be explicit. If daily lock action fails, live trading must remain blocked and operator must see exact failure. The current controller marks failed but must guarantee pre-execution gate sees lock state and kill/new_orders_paused state consistently.

**Required completion:**

- Add `DailyLockOrchestrator` as one source-of-truth:
  - evaluate threshold;
  - lock state;
  - create exactly-once action;
  - execute runtime action;
  - verify postcondition;
  - emit incident if action fails.
- `close_all_and_stop` failure must set `kill_switch=true` and `new_orders_paused=true` until operator clears.
- `reset-lock` must require:
  - admin;
  - reason;
  - no unresolved unknown orders;
  - no critical incidents;
  - broker reconciliation green.

---

### P0.7 — Live startup preflight should include symbol-specific proof

**Files:**

- `apps/api/app/services/live_start_preflight.py`
- `apps/api/app/services/live_readiness_guard.py`
- `apps/api/app/services/bot_service.py`

**Current finding:**

Preflight validates provider, capability proof, approved policy, daily state freshness, no daily lock, no unknown orders, no critical incident.

But `require_capability_proof()` is called without bot symbol. It defaults to `EURUSD` in the base proof. If the bot trades BTCUSDT, XAUUSD, GBPJPY, etc., the proof may not validate the actual live instrument.

**Required completion:**

- Pass `symbol=bot.symbol` to capability proof.
- Also pass `timeframe` if provider has data subscription per timeframe.
- Preflight must validate account currency and instrument tradability for that exact bot symbol.

---

## 2. Module-by-module audit

### 2.1 `apps/api` — API control plane

**Good:**

- Modular routers exist: auth, users, workspaces, broker connections, bots, signals, live trading, risk policy, experiments, qa parity.
- Production rejects `enable_legacy_routes=true`.
- Runtime registry is initialized in lifespan.
- Reconciliation daemon can start from API lifespan.
- Health endpoints expose daemon and legacy status.

**Missing/harden:**

- Add `/health/live-hard` endpoint that returns `fail` if any running live bot has unresolved unknowns, stale daily state, stale broker account snapshot, open critical incident, or stale capability proof.
- Add operator endpoint for broker capability proof history.
- Add emergency `pause_new_orders` separate from full kill switch.
- Add RBAC beyond `is_superuser` for live reset operations: `operator`, `risk_manager`, `admin`.

---

### 2.2 `apps/api/app/services/bot_service.py` — Bot runtime orchestration

**Good:**

- Hooks for order ledger, reconciliation, daily lock, incidents.
- Live mode detects stub/degraded provider modes.
- Runtime hooks can persist order intent and unknown events.

**Missing/harden:**

- Remove `_register_stub` from any production/live import path. It can exist only in test module.
- Fail API startup if `trading_core` import fails in production, not just warn.
- Ensure live bot cannot start unless `run_live_start_preflight()` was executed in same start command and recorded as audit event.
- Add explicit `LiveStartReceipt` table:
  - bot id;
  - provider proof hash;
  - policy version;
  - daily state id;
  - reconciliation status;
  - operator id;
  - timestamp.

---

### 2.3 `services/trading-core` — Trading brain and risk engine

**Good:**

- Rich engine set exists: entry logic, wave detector, risk manager, session manager, signal coordinator, performance tracker, meta-learning, autonomous engine.
- Runtime layer has `BotRuntime`, `RuntimeFactory`, `RuntimeRegistry`, `PreExecutionGate`, `FrozenContextContract`.
- Risk layer includes broker-native risk context, instrument spec, pip value, position sizing, exposure guard.

**Missing/harden:**

- Live path should not allow LLM/orchestrator fallback or creative decision without deterministic strategy rule snapshot.
- Add strategy stage policy:
  - `DRAFT`: backtest only;
  - `PAPER`: paper only;
  - `DEMO`: demo only;
  - `LIVE_CANARY`: capped live;
  - `LIVE_FULL`: approved scaling.
- Every signal should carry immutable `strategy_version`, `model_version`, `feature_snapshot_hash`, `risk_policy_hash`.

---

### 2.4 `services/execution-service` — Broker/execution layer

**Good:**

- Provider interface is clean.
- `OrderRequest`, `OrderResult`, `ExecutionCommand`, `PreExecutionContext` are explicit.
- Provider split exists for `paper`, `ctrader`, `mt5`, `bybit`, demo/live wrappers.
- Execution engine has gate, timeout handling, mark-submitting hook, unknown hook.

**Missing/harden:**

- True live providers need SDK-native implementation, not wrappers around incomplete engine classes.
- `OrderResult.success=True` should require `submit_status=ACKED` and valid fill or accepted pending state. Current cTrader path assumes filled when `executionPrice > 0`.
- Add partial fill support.
- Add pending order lifecycle support if order type is not market.
- Add broker error taxonomy:
  - auth;
  - market closed;
  - invalid volume;
  - insufficient margin;
  - price changed/slippage;
  - duplicate client order;
  - network timeout;
  - unknown.

---

### 2.5 `apps/web` — Operator / frontend

**Good:**

- Vietnamese UI is already present.
- Live pages exist:
  - `live-control-center`;
  - `live-orders`;
  - runtime control;
  - daily lock panel;
  - reconciliation timeline;
  - unknown orders panel;
  - execution receipt drawer.

**Missing/harden:**

- Add explicit “LIVE SAFETY STATUS” banner: green/yellow/red.
- Add forced confirmation for reset kill/daily lock.
- Add broker capability proof panel.
- Add per-order ledger timeline with immutable event hash.
- Add “why blocked” explainer from `PreExecutionGate`.
- Add operator action audit trail in UI.

---

### 2.6 Infra / CI / deployment

**Good:**

- Root `docker-compose.yml` is now empty and points to infra compose files.
- Production compose uses API/web/nginx/postgres/redis only.
- CI scripts exist for legacy drift, live import boundary, no stub provider, safety closure, frontend API contract, market data quality.
- Alembic chain is linear through `0018_daily_lock_actions`.

**Missing/harden:**

- Prod compose should include:
  - separate reconciliation worker process, not only API lifespan task;
  - metrics exporter;
  - alertmanager/webhook integration;
  - broker connectivity healthcheck;
  - DB migration job before API starts.
- CI should run full Postgres migration upgrade/downgrade cycle.
- Add smoke test that boots prod compose and verifies no legacy containers.

---

### 2.7 Legacy `backend/` and `frontend/`

**Good:**

- Marked deprecated.
- Root compose does not run them.
- API blocks legacy routes in production.

**Still risky:**

- Legacy files remain in repo and include trading engines/providers that may confuse devs or accidental imports.

**Required completion:**

- Move to `archive/legacy_backend_do_not_import/` or delete from production source tree.
- Add import boundary CI: no code in `apps/` or `services/` may import from root `backend`.
- Add CODEOWNERS restriction for legacy edits.

---

## 3. Recommended patch order

### Patch P0-A — Capability Proof Hardlock

Files:

```text
services/execution-service/execution_service/providers/base.py
apps/api/app/services/live_readiness_guard.py
apps/api/app/services/live_start_preflight.py
apps/api/alembic/versions/0019_broker_capability_proofs.py
apps/api/app/models/__init__.py
apps/api/tests/test_broker_capability_proof_hardlock.py
```

Acceptance:

- live preflight fails if margin estimate or execution lookup is not proven;
- proof is symbol-specific;
- proof is persisted and shown in dashboard.

---

### Patch P0-B — True cTrader Live Adapter

Files:

```text
services/execution-service/execution_service/providers/ctrader_openapi_live.py
services/execution-service/execution_service/providers/ctrader_openapi_auth.py
services/execution-service/execution_service/providers/ctrader_openapi_mapper.py
services/execution-service/execution_service/providers/ctrader_openapi_reconcile.py
services/execution-service/execution_service/providers/__init__.py
services/execution-service/tests/test_ctrader_openapi_live_contract.py
```

Acceptance:

- live provider does not use data engine as execution adapter;
- client order id roundtrip is proven;
- order/deal/position lookup works by idempotency key;
- close-all verifies zero remaining positions.

---

### Patch P0-C — Order Lifecycle Resolver

Files:

```text
apps/api/app/services/order_lifecycle_resolver.py
apps/api/app/services/order_ledger_service.py
apps/api/app/services/safety_ledger.py
apps/api/app/workers/reconciliation_daemon.py
apps/api/tests/test_order_lifecycle_resolver.py
```

Acceptance:

- unknown order resolution persists receipt + transition + projection before queue resolved;
- duplicate broker receipts are idempotent;
- illegal transition creates critical incident.

---

### Patch P0-D — Broker Snapshot Hash Contract

Files:

```text
services/trading-core/trading_core/risk/broker_snapshot.py
services/trading-core/trading_core/risk/risk_context_builder.py
services/trading-core/trading_core/runtime/pre_execution_gate.py
services/trading-core/trading_core/runtime/frozen_context_contract.py
services/trading-core/tests/test_broker_snapshot_hash_contract.py
```

Acceptance:

- live gate rejects fallback quote/spec;
- quote/spec hashes are stable and canonical;
- request/context mismatch blocks execution.

---

### Patch P0-E — Daily Lock Orchestrator

Files:

```text
apps/api/app/services/daily_lock_orchestrator.py
apps/api/app/services/daily_lock_runtime_controller.py
apps/api/app/routers/live_trading.py
apps/api/tests/test_daily_lock_orchestrator.py
```

Acceptance:

- threshold hit produces exactly one lock action;
- close failure sets kill switch and critical incident;
- reset lock requires all safety preconditions.

---

### Patch P0-F — Production Worker + Health Closure

Files:

```text
infra/docker/docker-compose.prod.yml
apps/api/app/main.py
apps/api/app/routers/admin.py
apps/api/tests/test_health_live_hard.py
.github/workflows/services-ci.yml
```

Acceptance:

- reconciliation daemon runs as dedicated service;
- `/health/live-hard` fails on unresolved unknowns/critical incidents/stale proof;
- prod boot smoke test confirms no legacy service.

---

## 4. Production readiness checklist

Before real money:

- [ ] broker capability proof persists and includes margin + execution lookup;
- [ ] cTrader/MT5/Bybit live providers are true SDK/API adapters;
- [ ] live start preflight is symbol/account/policy specific;
- [ ] order lifecycle resolver handles crash/timeout/partial/duplicate;
- [ ] unknown queue resolves only after ledger commit;
- [ ] daily TP/loss lock is exactly-once and fail-closed;
- [ ] broker quote/spec snapshots are canonical and hashed;
- [ ] all live bots show green live-hard health;
- [ ] no root legacy import possible;
- [ ] production compose includes separate worker;
- [ ] CI uses Postgres migration full cycle;
- [ ] operator dashboard exposes proof, unknowns, incidents, kill/daily reset audit.

---

## 5. Final recommendation

Do **not** run this repo with unsupervised real funds yet.

Safe sequence:

1. run full test suite in Postgres-backed CI;
2. complete P0-A → P0-F;
3. run paper for 7 days;
4. run demo for 14 days with broker reconciliation enabled;
5. run live canary with tiny size and daily TP/loss hardlock;
6. only then consider scaling capital.

This repo is close, but the final missing layer is **broker-truth closure**: every live decision, risk estimate, order submission, broker response, unknown resolution, daily lock and operator reset must be provable from broker-native data and persisted as immutable ledger evidence.
