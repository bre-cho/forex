# FOREX MAIN 9(1) — Deep File-by-File / Module-by-Module Completion Audit

**Repo audited:** `/mnt/data/forex-main-9(1).zip`  
**Audit scope:** architecture, API, trading-core, execution-service, broker providers, risk engine, daily TP/loss lock, order lifecycle, reconciliation, frontend/operator UI, CI, legacy isolation.  
**Verdict:** **NEAR-LIVE, BUT NOT READY FOR UNATTENDED REAL-MONEY TRADING.**

This version is materially stronger than prior builds: root compose no longer starts legacy, Alembic has order lifecycle + receipt + reconciliation + daily lock actions, execution has live-only `ExecutionCommand`, frozen context validation, unknown-order queue/daemon, and live start preflight. The remaining gap is not “more features”; it is **hard production closure**: broker-native truth, atomic lifecycle, exactly-once runtime lock, and proof that every live path fails closed under broker/API/network failures.

---

## 1. High-level production readiness score

| Area | Score | Status |
|---|---:|---|
| Monorepo separation | 8.5/10 | Good. `apps/api`, `apps/web`, `services/*` are separated. Root `docker-compose.yml` disables legacy stack. |
| Broker provider contract | 6.5/10 | Good interface, but live wrappers for MT5/Bybit are still thin and cTrader depends on underlying engine methods that may be absent. |
| Execution lifecycle | 7.5/10 | Stronger: SUBMITTING hook, timeout → UNKNOWN queue, receipt enforcement. Still needs DB transaction/outbox closure. |
| Risk engine | 7/10 | Live requires broker spec + broker margin. Still needs conversion-rate/cross-currency and broker-native pip value validation. |
| Daily TP/loss lock | 7.5/10 | Policy + state + actions exist. Need guaranteed runtime integration and close-all compensation loop. |
| Reconciliation | 7/10 | Queue, lease, daemon, critical incident, broker lookup exist. Need startup-owned worker deployment + source-of-truth projection test. |
| Frontend operator UI | 6.5/10 | Panels exist. Needs full live operations cockpit and manual reconcile/resolve flow. |
| CI / safety scripts | 8/10 | Many safety verifiers exist. Need full real-provider contract simulation and docker prod smoke. |
| Legacy isolation | 7.5/10 | Root compose safe, legacy folders remain. Need import boundary tests and production image exclusion proof. |

**Production status:** `LIVE-GUARDED / DEMO-READY`, not `LIVE-AUTONOMOUS`.

---

## 2. File/module inventory summary

Repo contains **354 files**. Key production modules:

### API layer
- `apps/api/app/main.py`
- `apps/api/app/routers/live_trading.py`
- `apps/api/app/routers/risk_policy.py`
- `apps/api/app/routers/broker_connections.py`
- `apps/api/app/services/live_start_preflight.py`
- `apps/api/app/services/live_readiness_guard.py`
- `apps/api/app/services/order_ledger_service.py`
- `apps/api/app/services/order_projection_service.py`
- `apps/api/app/services/reconciliation_queue_service.py`
- `apps/api/app/services/reconciliation_lease_service.py`
- `apps/api/app/workers/reconciliation_daemon.py`
- `apps/api/app/services/daily_profit_lock_engine.py`
- `apps/api/app/services/daily_lock_orchestrator.py`
- `apps/api/app/services/daily_lock_runtime_controller.py`
- `apps/api/app/services/daily_trading_state.py`
- `apps/api/app/services/policy_service.py`

### DB / migrations
- `0001_initial_schema.py` → base SaaS/trading schema
- `0003_live_trading_safety_ledger.py` → safety ledger
- `0004_order_idempotency_reservations.py` → idempotency reservations
- `0005_broker_order_attempts.py` → broker attempts
- `0006_order_state_transitions.py` → state transitions
- `0007_broker_execution_receipts.py` → execution receipts
- `0008_policy_approval_control_plane.py` → policy approval
- `0009_daily_profit_lock_policy.py` → daily TP policy
- `0010_order_state_machine.py` → lifecycle states
- `0011_account_snapshots_and_experiment_registry.py` → account snapshots / experiment registry
- `0012_order_idempotency_projection.py` → order idempotency projection
- `0013_reconciliation_queue.py` → unknown order queue
- `0014_orders_projection_and_transition_idempotency.py` → order projection + transition uniqueness
- `0015_broker_attempt_gate_context_hash.py` → frozen gate hash
- `0016_execution_receipt_contract.py` → receipt contract
- `0017_reconciliation_queue_lease.py` → lease/deadline/max attempts
- `0018_daily_lock_actions.py` → exactly-once lock actions

### Execution service
- `services/execution-service/execution_service/execution_engine.py`
- `services/execution-service/execution_service/order_router.py`
- `services/execution-service/execution_service/order_state_machine.py`
- `services/execution-service/execution_service/unknown_order_reconciler.py`
- `services/execution-service/execution_service/reconciliation_worker.py`
- `services/execution-service/execution_service/providers/base.py`
- `providers/ctrader.py`, `ctrader_live.py`, `ctrader_demo.py`
- `providers/mt5.py`, `mt5_live.py`, `mt5_demo.py`
- `providers/bybit.py`, `bybit_live.py`, `bybit_demo.py`
- `providers/paper.py`

### Trading core
- `services/trading-core/trading_core/runtime/bot_runtime.py`
- `runtime/pre_execution_gate.py`
- `runtime/frozen_context_contract.py`
- `runtime/runtime_factory.py`
- `runtime/runtime_registry.py`
- `risk/risk_context_builder.py`
- `risk/broker_native_risk_context.py`
- `risk/instrument_spec.py`
- `risk/position_sizing.py`
- `risk/daily_profit_policy.py`
- `data/market_data_quality.py`
- `engines/*` brain/strategy modules

### Frontend/admin
- `apps/web/components/live/DailyLockPanel.tsx`
- `apps/web/components/live/ExecutionReceiptDrawer.tsx`
- `apps/web/components/live/LiveReadinessPanel.tsx`
- `apps/web/components/live/ReconciliationTimeline.tsx`
- `apps/web/components/live/UnknownOrdersPanel.tsx`
- `apps/admin/app/broker-health/page.tsx`
- `apps/admin/app/operations-dashboard/page.tsx`
- `apps/admin/app/runtime/page.tsx`

### Legacy
- `backend/*`
- `frontend/*`

Legacy is now explicitly marked deprecated, and root compose has `services: {}`. Good improvement. Still, production Dockerfiles and CI must prove legacy is never importable from live services.

---

## 3. Critical findings by module

## 3.1 `docker-compose.yml` / infra

**Current state**
- Root compose intentionally contains no running services.
- Dev/prod compose moved to `infra/docker/docker-compose.dev.yml` and `infra/docker/docker-compose.prod.yml`.
- This fixes a prior critical risk where root compose could boot legacy `backend/` or `frontend/`.

**Remaining gap**
- Need prod smoke test that runs only `apps/api`, `apps/web`, service workers, Postgres/Redis and explicitly asserts legacy containers are absent.

**Patch**
- Add `scripts/ci/verify_prod_compose_no_legacy.sh`:
  - run `docker compose -f infra/docker/docker-compose.prod.yml config --services`
  - reject `backend`, `frontend`, `legacy`, `streamlit` service names
  - assert expected services: `api`, `web`, `postgres`, `redis`, `reconciliation-worker` or equivalent.

---

## 3.2 `apps/api/app/services/live_start_preflight.py`

**Current state**
- Performs provider readiness check.
- Requires capability proof.
- Requires approved active policy.
- Syncs broker equity fail-closed.
- Blocks active daily lock.
- Blocks unresolved unknown orders.
- Blocks open critical incidents.

**Strong points**
- This is the correct gateway for live start.
- Broker equity sync is fail-closed; no stale state fallback.

**Gaps**
1. Policy required keys are too small:
   - current required keys: `daily_take_profit`, `max_daily_loss_pct`, `max_margin_usage_pct`, `max_account_exposure_pct`
   - live policy should also require: `max_symbol_exposure_pct`, `max_correlated_usd_exposure_pct`, `max_spread_pips`, `max_slippage_pips`, `min_free_margin_after_order`, `stop_loss_required_in_live`, `max_open_positions`, `max_risk_amount_per_trade`, `lock_action_on_daily_tp`, `lock_action_on_daily_loss`.
2. Capability proof is persisted into `AuditLog`, but there is no dedicated immutable `broker_capability_proofs` table with `proof_hash`, `provider`, `account_id`, `symbol`, `status`, `expires_at`.
3. Does not prove reconciliation daemon is running before live start.

**Patch**
- Add required policy keys set `LIVE_POLICY_REQUIRED_KEYS_V2`.
- Add `ReconciliationDaemonHealthService` and require `_daemon_running == True` or external worker heartbeat table.
- Add DB table `broker_capability_proofs` instead of only `AuditLog`.

---

## 3.3 `apps/api/app/services/broker_capability_proof_service.py`

**Current state**
- Stores capability proof as `AuditLog`.
- Computes deterministic proof hash.

**Gap**
- AuditLog is not enough as a live gate source-of-truth. You need queryable proof status and expiration.

**Patch**
Create migration `0019_broker_capability_proofs.py`:
- `id`
- `bot_instance_id`
- `provider`
- `account_id`
- `symbol`
- `timeframe`
- `proof_hash`
- `proof_payload`
- `status`: `passed | failed | expired`
- `expires_at`
- `created_at`
- unique `(bot_instance_id, provider, account_id, symbol, timeframe, proof_hash)`

Then update live start to require proof within last 5–15 minutes.

---

## 3.4 `services/execution-service/execution_service/execution_engine.py`

**Current state**
- Live mode requires `ExecutionCommand`.
- Requires `brain_cycle_id`, `idempotency_key`, `pre_execution_context`.
- Verifies idempotency reservation.
- Requires frozen `gate_context` and `context_hash`.
- Validates context bindings.
- Marks SUBMITTING before broker call via hook.
- Timeout/error transitions to UNKNOWN queue via hook.
- Enforces live receipt contract.

**Strong points**
- This is close to a real trading execution gateway.
- Timeout → UNKNOWN queue is correct.

**Gaps**
1. SUBMITTING hook + broker submit are not in one durable outbox workflow. If hook succeeds but broker call never happens due to process death, daemon must classify it as `SUBMITTING_STALE_NO_BROKER_SUBMIT` instead of assuming broker unknown.
2. On broker exception, it enqueues unknown even if exception occurred before broker submit left the process. The payload needs `submit_phase`: `before_send | after_send | unknown_phase`.
3. `_enforce_live_receipt_contract` treats success as requiring immediate `FILLED` or `PARTIAL`. Some brokers acknowledge market order but fill later. Current contract may force valid ACKED/PENDING into failure. Need support for `ACKED + PENDING_FILL` and then reconciliation.
4. Client order ID is set from request only, but cTrader adapter passes `request.comment`, not necessarily `request.client_order_id`.

**Patch**
- Add `SubmitOutbox` state:
  - `INTENT_RESERVED`
  - `SUBMITTING_PERSISTED`
  - `BROKER_SEND_STARTED`
  - `BROKER_SEND_RETURNED`
  - `ACKED_PENDING_FILL`
  - `FILLED`
  - `REJECTED`
  - `UNKNOWN_AFTER_SEND`
  - `UNKNOWN_BEFORE_SEND`
- Add submit phase markers around actual provider call.
- Receipt contract should allow:
  - `ACKED + PENDING_FILL` when broker order id exists.
  - queue reconciliation automatically.
- Force `request.client_order_id = idempotency_key`, `request.comment = idempotency_key` in live mode immediately before routing.

---

## 3.5 `services/execution-service/execution_service/providers/base.py`

**Current state**
- Defines `OrderRequest`, `OrderResult`, `PreExecutionContext`, `ExecutionCommand`, `BrokerProvider`.
- Live optional methods exist: `get_instrument_spec`, `estimate_margin`, `get_order_by_client_id`, `get_executions_by_client_id`, `close_all_positions`.

**Gap**
- Optional methods are effectively required in live, but the type contract does not separate `LiveBrokerProvider` from `BrokerProvider`.

**Patch**
- Create `LiveBrokerProviderProtocol` with required methods.
- Make `LiveReadinessGuard.assert_live_provider_contract()` require protocol compliance.
- Add `supports_client_order_id: bool` and `client_order_id_transport: comment | client_order_id | orderLinkId | magic`.

---

## 3.6 `services/execution-service/execution_service/providers/ctrader.py`

**Current state**
- Live mode fails closed if execution adapter unavailable.
- Connect checks account info and candle stream readiness.
- Requires `client_order_id` in live.
- `get_quote` fails in live if broker quote unavailable.
- Has lookup methods via client id and history fallback.

**Gaps**
1. `place_order()` sends `comment=request.comment`, not guaranteed to equal `request.client_order_id`.
2. The underlying `CTraderDataProvider` in `trading_core/engines/ctrader_provider.py` still appears to have fallback-like behavior and may not implement all execution methods (`get_instrument_spec`, `estimate_margin`, `get_order_by_client_id`).
3. `connect()` market readiness uses candles, not a true bid/ask quote. Later `get_quote()` is strict, but start preflight should also require live quote.
4. `broker_order_id = orderId or positionId`; for traceability, order ID and position ID should stay separate.

**Patch**
- In live path:
  - `client_id = request.client_order_id or request.idempotency_key`
  - reject if missing
  - pass `comment=client_id` and `client_order_id=client_id` where adapter supports it
- Add cTrader `LiveQuoteCapabilityProof`: bid, ask, timestamp, quote_id, spread.
- Add cTrader execution contract tests with fake live adapter returning: delayed ack, partial fill, reject, duplicate client order, history lookup.
- Do not collapse `positionId` into `broker_order_id`; preserve both.

---

## 3.7 `services/execution-service/execution_service/providers/ctrader_execution_adapter.py`

**Current state**
- Separates execution adapter from market data adapter.
- Fails closed when execution methods missing.
- Passes `comment` and `client_order_id` if underlying provider supports them.

**Gap**
- There is no explicit adapter method for `get_order_by_client_id`, `get_executions_by_client_id`, `estimate_margin`, `get_instrument_spec`, `get_quote`. Provider falls back to underlying `_provider` directly, bypassing adapter contract.

**Patch**
- Extend `CTraderExecutionAdapter` to include live reconciliation and risk methods.
- Add `CTraderLiveExecutionAdapterContractTest`.
- Remove direct `_provider` calls from `CTraderProvider` for live-required methods; go through adapter only.

---

## 3.8 `services/execution-service/execution_service/providers/mt5_live.py` and `mt5.py`

**Current state**
- `MT5LiveProvider` sets `kwargs["live"] = False`, calls demo constructor, then mutates `self.live = True`, `self.mode = "live"`.
- This bypasses the constructor’s live guard.

**Critical concern**
- This is a code smell. It may work, but it is not production-clean. A live provider should not instantiate through a demo-only guard and mutate itself.

**Patch**
- Refactor `MT5Provider.__init__(..., mode: Literal["demo", "live"], _allow_live=False)`.
- Or create `BaseMT5Provider`, `MT5DemoProvider`, `MT5LiveProvider` separately.
- `MT5LiveProvider` must verify:
  - terminal connected
  - account trade allowed
  - symbol trade mode enabled
  - order check passes
  - margin calculation via `order_calc_margin`
  - client id trace via `magic/comment`.

---

## 3.9 `services/execution-service/execution_service/providers/bybit_live.py` and `bybit.py`

**Current state**
- `BybitProvider` constructor rejects `testnet=False` and says use `BybitLiveProvider`.
- `BybitLiveProvider` passes `testnet=True` to constructor, then sets `testnet=False` and `mode="live"`.

**Critical concern**
- Same pattern as MT5. It bypasses demo-only constructor with mutation.

**Patch**
- Refactor into `BaseBybitProvider`, `BybitDemoProvider`, `BybitLiveProvider`.
- Live provider constructor should directly initialize live mode and reject testnet keys.
- Require `orderLinkId == idempotency_key` and direct lookup by `orderLinkId`.
- Add test for live constructor not using testnet mutation.

---

## 3.10 `services/trading-core/trading_core/runtime/bot_runtime.py`

**Current state**
- Runtime live path fetches live quote, quote timestamp, account info, broker spec, and performs broker-native-ish sizing.
- Live mode requires SL.
- Emits broker account snapshot event.
- Has runtime preflight and reconciliation-first logic.

**Gaps**
1. The risk path still computes `instrument_spec` with defaults if broker payload lacks fields. In live mode, defaulting `pip_size=0.0001`, `pip_value=10`, `contract_size=100000`, `margin_rate=0.01`, etc. can silently misprice risk if broker payload is partial.
2. Cross-currency pip value is not fully broker-native. `pip_value_per_lot_usd` assumes USD unless broker spec says otherwise.
3. There is a lot of logic in one runtime file. Production should split into:
   - `LiveSignalValidator`
   - `BrokerSnapshotBuilder`
   - `RiskContextAssembler`
   - `FrozenGateContextBuilder`
   - `ExecutionCommandBuilder`
   - `RuntimeEventEmitter`
4. Runtime references provider mode `stub` in many places; tests should assert live mode cannot proceed when any mode is `stub/paper/degraded/unavailable`.

**Patch**
- In live mode, require all broker spec fields exactly; no default values.
- Add `BrokerSpecValidator`.
- Add `QuoteFreshnessValidator` with broker server time, not local time.
- Split monolith into smaller modules.

---

## 3.11 `services/trading-core/trading_core/runtime/pre_execution_gate.py`

**Current state**
- Blocks kill switch, daily lock, paused orders, broker disconnected, stub/degraded provider, unapproved policy, missing policy hash, missing instrument spec hash, missing quote ID/timestamp, missing SL, stale data, daily loss, daily TP, margin/exposure, lot limits, idempotency duplicates.

**Strong points**
- This is the correct hard gate layer.

**Gaps**
1. Daily TP is checked as `daily_profit_amount >= target`, but if a trade closes and profit hits target, the runtime must immediately persist lock and pause/close positions. Gate alone only blocks next order.
2. `policy_hash` only proves presence. It should be bound to exact approved `PolicyVersion.id/version/status`.
3. Need hard block for `quote_timestamp` age using broker server time, not local `time.time()`.
4. Need block if `reconciliation_queue.has_unresolved(bot_id)` before any new order.

**Patch**
- Add `unknown_orders_unresolved` to gate context.
- Add `approved_policy_version_id`, `policy_approved_at`, `policy_hash`, `policy_status=active` binding.
- Add `broker_server_time` and `quote_age_seconds` computed from broker server time.

---

## 3.12 `services/trading-core/trading_core/runtime/frozen_context_contract.py`

**Current state**
- Validates bot id, idempotency, brain cycle, account id, broker name, policy version.
- Validates gate context schema, symbol/side/volume, account/broker/policy, policy hash, order type, price/SL/TP, context hash.

**Strong points**
- Important hardening exists.

**Gaps**
- Does not require `quote_id`, `quote_timestamp`, `instrument_spec_hash`, `broker_snapshot_hash`, `risk_context_hash` even though pre-execution gate expects some of them.
- Price tolerance is 5%, too wide for Forex/crypto execution binding. In live trading, request price should match quote/entry within configured slippage.

**Patch**
- Require in gate context:
  - `quote_id`
  - `quote_timestamp`
  - `instrument_spec_hash`
  - `broker_snapshot_hash`
  - `risk_context_hash`
  - `approved_policy_version_id`
  - `broker_account_snapshot_id` or snapshot hash
- Replace 5% price tolerance with `max_slippage_pips` or `max_price_deviation_bps`.

---

## 3.13 `services/trading-core/trading_core/risk/risk_context_builder.py`

**Current state**
- Live mode requires instrument spec and broker margin estimate.
- Computes margin, exposure, symbol exposure, correlated USD exposure, pip value, max loss.

**Gaps**
1. Exposure calculation is not broker-native; uses notional approximation.
2. Correlated exposure is simple string check for `USD`.
3. Pip value can be wrong for account currencies not USD.
4. Open positions may have broker-specific fields not normalized.

**Patch**
- Add `BrokerPositionNormalizer`.
- Add `CurrencyConversionService` for account currency conversion.
- Add correlation bucket model:
  - USD bucket
  - JPY bucket
  - crypto beta bucket
  - same-base/same-quote exposure
- Use broker margin + broker pip/tick value where possible.

---

## 3.14 `services/trading-core/trading_core/risk/broker_native_risk_context.py`

**Current state**
- Enforces live margin estimate through provider method.
- Fails if estimate unavailable or <=0.

**Gap**
- Only covers margin. Live-native risk should also verify tick value, min/max/step, leverage/margin mode, trading status, spread, session state.

**Patch**
- Rename to `broker_native_risk_probe.py` with:
  - `estimate_margin_required`
  - `get_tick_value`
  - `get_contract_size`
  - `get_lot_constraints`
  - `get_leverage/margin mode`
  - `get_trading_status`

---

## 3.15 `apps/api/app/services/daily_profit_lock_engine.py`, `daily_lock_orchestrator.py`, `daily_lock_runtime_controller.py`

**Current state**
- Daily TP policy supports fixed amount, percent equity, capital tier.
- Orchestrator locks bot with postcondition verification.
- Runtime controller supports `stop_new_orders`, `close_all_and_stop`, `reduce_risk_only` with exactly-once `daily_lock_actions`.

**Strong points**
- This is a real production pattern.

**Gaps**
1. `DailyLockOrchestrator` uses in-process advisory lock only. In multi-instance API deployments, this is not enough. Need DB advisory lock or row-level lock.
2. Runtime controller with `close_all_and_stop` needs compensation if close partially fails.
3. `positions_after` is set to `0` optimistically before verifying all broker positions closed.
4. Need a `daily_lock_action_compensator` worker to retry failed lock actions.

**Patch**
- Use Postgres advisory lock: `pg_advisory_xact_lock(hash(bot_id, trading_day))`.
- Add `daily_lock_action_compensator.py` worker.
- Set `positions_after` from actual broker post-close count.
- If close fails, set status `compensating` and keep bot locked + new orders paused.

---

## 3.16 `services/execution-service/execution_service/unknown_order_reconciler.py`

**Current state**
- Tries direct lookup by client order id.
- Falls back to history/open position matching.
- Returns filled/rejected/still_unknown/error/failed_needs_operator.

**Gaps**
1. `failed_needs_operator` after max retries is good, but live mode should distinguish:
   - lookup unsupported
   - provider unavailable
   - broker API rate-limited
   - client id not found but position exists by symbol/volume/time
   - ambiguous duplicate match
2. It does not return confidence/ambiguity class.
3. History fallback by `comment/clientMsgId` is only as good as client ID transport.

**Patch**
- Add outcome reasons:
  - `filled_exact_client_id`
  - `rejected_exact_client_id`
  - `ambiguous_multiple_matches`
  - `not_found_after_broker_confirmed_lookup`
  - `provider_lookup_unavailable`
  - `broker_rate_limited`
- Add `match_confidence` and `matched_fields`.
- Ambiguous match must open critical incident and block new orders.

---

## 3.17 `apps/api/app/workers/reconciliation_daemon.py`

**Current state**
- Polls queue.
- Uses lease service.
- Attempts broker reconcile after 30s.
- Dead-letter + critical incident + daily lock after deadline/max attempts.
- Persists ledger before marking queue resolved.

**Strong points**
- This is the correct shape.

**Gaps**
1. It depends on in-memory `runtime_registry.get(bot_id)` to find provider. If the bot runtime process is down, reconciliation cannot query broker and will escalate. Production should reconstruct provider from DB credentials or run as sidecar in same runtime process.
2. No heartbeat table proving daemon is alive for live preflight.
3. No operator API to manually resolve dead-letter with broker evidence.

**Patch**
- Add `worker_heartbeats` table.
- Add `ProviderFactoryFromBotConnection` for daemon to create provider from encrypted credentials when runtime absent.
- Add manual resolution endpoint:
  - `POST /live/reconciliation/{id}/resolve`
  - requires admin/operator role
  - requires broker proof payload/hash
  - writes ledger transition.

---

## 3.18 `apps/api/app/services/order_ledger_service.py` and `order_projection_service.py`

**Current state**
- Handles lifecycle event persistence and projection into `orders`.
- There are tests for crash-after-submit and ledger integration.

**Remaining production gap**
- Need a strict event-sourced state machine: every terminal order state must be derivable from immutable transitions + receipts. `orders` should be projection only.

**Patch**
- Add invariant checker:
  - every `orders.current_state` must match latest `order_state_transitions`.
  - every filled order must have receipt.
  - every unknown order must have reconciliation queue item.
  - every rejected order must have broker/error reason.
- Add nightly job `verify_order_ledger_integrity.py`.

---

## 3.19 `apps/web/components/live/*` and `apps/admin/app/*`

**Current state**
- Live panels exist: daily lock, execution receipt, readiness, reconciliation timeline, unknown orders.
- Admin has broker health and operations pages.

**Gaps**
- Operator UI still needs full control-plane flows:
  - approve policy
  - start live preflight details
  - view capability proof hash
  - acknowledge critical incident
  - manual reconcile with evidence
  - close all positions emergency action
  - unlock/reset next trading day
  - export audit bundle.

**Patch**
- Add `LiveOpsCockpit` page:
  - readiness checks
  - broker proof
  - daily lock state
  - unknown orders
  - critical incidents
  - emergency actions
  - audit export.

---

## 3.20 `backend/*` legacy

**Current state**
- Legacy backend remains with old engines and routes.
- Marked deprecated.
- Root compose no longer starts it.

**Risk**
- Legacy engine files include duplicated trading logic. If import paths leak, production behavior may diverge.

**Patch**
- Add `legacy_import_guard.py` test:
  - scan `apps/` and `services/` for imports from `backend.` or `frontend.`
  - fail CI if found.
- Exclude `backend/` and `frontend/` from production Docker build context.
- Move legacy to `legacy/` or archive zip outside app build.

---

# 4. P0 patch plan — must do before real-money live

## P0-A — Live Broker Contract Closure

**Goal:** no live provider can start unless it proves execution, quote, account, spec, margin, and client order lookup.

Files:
- `services/execution-service/execution_service/providers/base.py`
- `providers/ctrader.py`
- `providers/ctrader_execution_adapter.py`
- `providers/mt5_live.py`
- `providers/bybit_live.py`
- `apps/api/app/services/live_readiness_guard.py`
- `apps/api/app/services/live_start_preflight.py`

Required changes:
1. Add `LiveBrokerProviderProtocol`.
2. Add mandatory methods:
   - `get_quote`
   - `get_server_time`
   - `get_instrument_spec`
   - `estimate_margin`
   - `get_order_by_client_id`
   - `get_executions_by_client_id`
   - `close_all_positions`
3. Remove mutation-based live wrappers for MT5/Bybit.
4. Require client-order-id transport proof.

Acceptance tests:
- live provider with missing quote → start blocked.
- live provider with no client order lookup → start blocked.
- MT5/Bybit live constructors do not pass through demo-only mutation.

---

## P0-B — Frozen Gate Context V2

Files:
- `services/trading-core/trading_core/runtime/pre_execution_gate.py`
- `services/trading-core/trading_core/runtime/frozen_context_contract.py`
- `services/trading-core/trading_core/runtime/bot_runtime.py`
- `services/execution-service/execution_service/execution_engine.py`

Required context fields:
- `schema_version = gate_context_v2`
- `bot_instance_id`
- `brain_cycle_id`
- `idempotency_key`
- `symbol`
- `side`
- `requested_volume`
- `approved_volume`
- `order_type`
- `entry_price`
- `stop_loss`
- `take_profit`
- `account_id`
- `broker_name`
- `broker_account_snapshot_hash`
- `instrument_spec_hash`
- `quote_id`
- `quote_timestamp`
- `broker_server_time`
- `risk_context_hash`
- `policy_version_id`
- `policy_hash`

Acceptance tests:
- missing any V2 field blocks live execution.
- symbol/side/volume/SL/TP mismatch blocks.
- hash mismatch blocks.
- quote too old blocks.

---

## P0-C — Atomic Submit Outbox + Order Ledger Source-of-Truth

Files:
- `apps/api/alembic/versions/0019_submit_outbox.py`
- `apps/api/app/services/order_ledger_service.py`
- `services/execution-service/execution_service/execution_engine.py`
- `apps/api/app/workers/reconciliation_daemon.py`

DB table:
- `submit_outbox`
  - `bot_instance_id`
  - `idempotency_key`
  - `phase`
  - `request_hash`
  - `provider`
  - `created_at`
  - `updated_at`

Required behavior:
- Persist `BROKER_SEND_STARTED` immediately before broker submit.
- Persist `BROKER_SEND_RETURNED` after response.
- If process dies after `BROKER_SEND_STARTED`, reconciliation treats it as true unknown.
- If process dies before `BROKER_SEND_STARTED`, mark safe failure, not broker unknown.

Acceptance tests:
- crash before send → no broker lookup required, order failed safely.
- crash after send → unknown queue created.
- broker timeout → unknown queue + incident path.

---

## P0-D — Daily TP/Loss Runtime Lock Closure

Files:
- `apps/api/app/services/daily_profit_lock_engine.py`
- `apps/api/app/services/daily_lock_orchestrator.py`
- `apps/api/app/services/daily_lock_runtime_controller.py`
- `apps/api/app/workers/daily_lock_action_compensator.py` new

Required behavior:
- When daily TP/loss hit:
  - persist lock exactly once
  - pause new orders immediately
  - execute configured action
  - if close-all incomplete, set compensating state
  - keep bot locked until operator/day reset

Acceptance tests:
- concurrent TP events create one lock action.
- close-all partial failure retries.
- new orders blocked while action in failed/compensating.

---

## P0-E — Reconciliation Worker Independence

Files:
- `apps/api/app/workers/reconciliation_daemon.py`
- `apps/api/app/services/reconciliation_queue_service.py`
- `apps/api/app/services/broker_connection_provider_factory.py` new
- `apps/api/app/routers/live_trading.py`

Required behavior:
- Daemon can query broker even if runtime registry is empty.
- Worker heartbeat recorded.
- Live preflight requires daemon healthy.
- Operator manual resolution endpoint exists.

Acceptance tests:
- runtime down + provider creds available → daemon reconciles.
- daemon not running → live start blocked.
- dead-letter manual resolve writes immutable ledger transition.

---

# 5. P1 patch plan — production hardening

## P1-A — Risk Engine V2
- Add conversion-rate service for account currency.
- Normalize broker positions.
- Replace string-based USD correlation with exposure buckets.
- Require broker-native tick/pip value.

## P1-B — Live Ops Cockpit
- Add one page showing readiness, policy, broker proof, daily lock, unknown orders, critical incidents, emergency actions.

## P1-C — CI contract simulation
- Fake live broker with scenarios:
  - ack pending fill
  - duplicate client ID
  - timeout after send
  - reject with broker code
  - partial fill
  - lookup unavailable
  - ambiguous lookup.

## P1-D — Production Docker smoke
- Assert legacy absent.
- Assert migrations single head.
- Assert worker heartbeat.
- Assert API health / live-hard endpoint.

---

# 6. P2 patch plan — autonomous live trading maturity

- Portfolio-level risk manager across all bots.
- News blackout provider integration.
- Broker session calendar.
- Post-trade analytics and slippage attribution.
- Strategy tournament isolation: no auto-promote to live without human approval.
- Kill-switch with hardware/API independent path.
- Immutable audit bundle export per trading day.

---

# 7. Final live trading readiness checklist

Before real-money trading, all must be true:

- [ ] Live provider contract passes for the selected broker.
- [ ] Client order ID/idempotency is transported to broker and lookup works.
- [ ] Broker quote is true bid/ask, fresh, timestamped, and hash-bound.
- [ ] Broker instrument spec is complete; no live defaults.
- [ ] Margin estimate is broker-native.
- [ ] Frozen gate context V2 validates all immutable fields.
- [ ] Submit outbox distinguishes before-send vs after-send crash.
- [ ] Unknown order daemon runs independently and has heartbeat.
- [ ] Daily TP/loss lock pauses new orders and closes/reduces positions exactly once.
- [ ] Operator can manually resolve dead-letter with evidence.
- [ ] Legacy stack is excluded from prod images and imports.
- [ ] CI covers broker failure scenarios.
- [ ] Live start preflight blocks on any unresolved risk.

---

## 8. Recommended next build

**BUILD P10 — LIVE BROKER CONTRACT + FROZEN GATE V2 PATCH**

This is the highest-leverage next patch because it closes the most dangerous class of bugs: execution request not perfectly bound to broker account, quote, policy, risk snapshot, symbol, side, volume, and client order identity.

Suggested patch order:
1. Add `LiveBrokerProviderProtocol`.
2. Refactor MT5/Bybit live wrappers.
3. Extend cTrader adapter contract.
4. Add broker capability proof table.
5. Implement GateContextV2.
6. Add tests for missing/mismatched frozen fields.
7. Update live preflight to require proof + daemon heartbeat.

**Final verdict:** The codebase is now a serious live-trading candidate, but it should remain in **demo/paper/live-sandbox** until P0-A through P0-E are complete and verified.
