# Incident Handling Runbook

This runbook documents the response procedure for the most common production
incidents in the Forex Trading Platform.  Keep this open during on-call shifts.

---

## 1. UNKNOWN_ORDER — Order Status Cannot Be Confirmed

**Symptom**: An order was submitted to the broker but neither a fill confirmation
nor a rejection was received.  The DB state shows `submit_status=UNKNOWN`.

**Impact**: Capital may be deployed without a DB record.  Risk system cannot
account for the open position.

**Response**:

1. Check the `submit_outbox` table for the affected `idempotency_key`:
   ```sql
   SELECT * FROM submit_outbox WHERE bot_instance_id = '<bot_id>'
     AND phase NOT IN ('SUBMITTED', 'ROLLED_BACK')
   ORDER BY updated_at DESC LIMIT 10;
   ```

2. Call the manual reconciliation endpoint to force a broker check:
   ```
   POST /v1/workspaces/{workspace_id}/bots/{bot_id}/reconcile
   ```

3. If the order is found at the broker (FILLED), update the `order_attempts`
   row via the admin API or directly via SQL:
   ```sql
   UPDATE order_attempts
     SET fill_status='FILLED', submit_status='ACKED', current_state='FILL_CONFIRMED'
   WHERE idempotency_key = '<key>';
   ```

4. If the order is NOT found at the broker (no position exists), run:
   ```
   POST /v1/admin/action-approvals  (incident_type=unknown_order_not_found)
   ```
   This creates an ActionApproval record for ops review before rolling back.

5. Monitor `trading_incidents` for any newly created incidents of type
   `unknown_order` over the next 15 minutes.

---

## 2. RECONCILIATION_MISMATCH — DB ≠ Broker Position

**Symptom**: The reconciliation worker detects that positions held by the broker
differ from what the DB `trades` table records.

**Impact**: Risk exposure is miscalculated; lot sizing may be wrong.

**Response**:

1. Query the reconciliation queue for recent mismatches:
   ```sql
   SELECT * FROM reconciliation_queue_items
   WHERE status = 'MISMATCH' AND bot_instance_id = '<bot_id>'
   ORDER BY created_at DESC LIMIT 5;
   ```

2. Compare with broker positions via the position endpoint:
   ```
   GET /v1/workspaces/{workspace_id}/bots/{bot_id}/positions
   ```

3. For each mismatch, create an ActionApproval for the ops team:
   ```
   POST /v1/workspaces/{workspace_id}/action-approvals
   Body: { "action_type": "reconciliation_override", "payload": { ... } }
   ```

4. Once approved, apply the correction via:
   ```
   POST /v1/admin/reconciliation/{item_id}/apply
   ```

5. If the discrepancy is large (>5% of account equity), consider pausing the
   bot until the root cause is identified:
   ```
   POST /v1/workspaces/{workspace_id}/bots/{bot_id}/pause
   ```

---

## 3. DAILY_LOCK_TRIGGERED — Trading Halted by Daily Loss Limit

**Symptom**: The bot stops generating order signals.  The `/health/deep` endpoint
shows `daily_locked: true` for the affected bot.

**Impact**: No new orders until the lock is manually reviewed and reset.

**Response**:

1. Query the daily trading state:
   ```sql
   SELECT * FROM daily_trading_states
   WHERE bot_instance_id = '<bot_id>'
   ORDER BY trading_day DESC LIMIT 1;
   ```

2. Review the `reason` field.  Common values:
   - `daily_loss_limit_exceeded` — daily P&L crossed the configured threshold
   - `reconciliation_incident` — reconciliation mismatch triggered safety lock
   - `kill_switch` — manually triggered

3. To reset the lock after review (requires admin role):
   ```
   DELETE /v1/workspaces/{workspace_id}/bots/{bot_id}/daily-lock
   ```
   Or via SQL (only in emergency with dual approval):
   ```sql
   UPDATE daily_trading_states
     SET locked=false, reason=NULL, updated_at=NOW()
   WHERE bot_instance_id='<bot_id>' AND trading_day='<today>';
   ```

4. Adjust the `daily_loss_limit_pct` in `bot_instance_configs` if the threshold
   needs tuning before restarting.

---

## 4. PROVIDER_DISCONNECTED — Broker Connection Lost

**Symptom**: Bot heartbeat detects broker connection failure.  The `broker_health`
metadata on the bot shows `status: disconnected`.

**Impact**: Market data unavailable; new orders cannot be placed; open positions
cannot be managed.

**Response**:

1. Check broker health via:
   ```
   GET /v1/workspaces/{workspace_id}/bots/{bot_id}/snapshot
   ```
   Look for `broker_health.status` and `broker_health.last_error`.

2. The broker heartbeat loop (`_broker_heartbeat_loop`) automatically attempts
   reconnection with exponential back-off (5s → 30s → 120s).  Wait for up to
   3 minutes for automatic recovery.

3. If auto-reconnect fails, stop and restart the bot:
   ```
   POST /v1/workspaces/{workspace_id}/bots/{bot_id}/stop
   POST /v1/workspaces/{workspace_id}/bots/{bot_id}/start
   ```

4. For CTrader: verify the OAuth token has not expired.  The token refresher
   runs automatically but may fail if the refresh endpoint is down.
   Manually rotate credentials in `broker_connections` if needed.

5. For MT5 (Windows-only via bridge): check the `mt5-bridge` service is running
   on the Windows host.  Verify `/health` on `http://<bridge-host>:8765/health`.

6. For Bybit: check the API key is not rate-limited or revoked in the Bybit
   dashboard.  Verify `testnet` flag matches the environment.

7. Monitor open positions manually at the broker while disconnected.

---

## 5. SUBMIT_OUTBOX_STUCK — Orders Not Being Submitted

**Symptom**: The `submit_outbox_recovery_worker` heartbeat is stale or stopped.
Orders accumulate in the outbox in `PENDING` phase.

**Impact**: New order signals are queued but not sent to the broker.

**Response**:

1. Check worker health:
   ```
   GET /health/deep
   ```
   Look for `submit_outbox_recovery_worker: false`.

2. Check the last heartbeat:
   ```sql
   SELECT * FROM worker_heartbeats
   WHERE worker_name='submit_outbox_recovery_worker'
   ORDER BY updated_at DESC LIMIT 1;
   ```

3. If the heartbeat is stale by >2 minutes, restart the API service.
   The worker starts automatically on API startup.

4. Check for stuck outbox items:
   ```sql
   SELECT phase, count(*) FROM submit_outbox
   WHERE created_at > NOW() - INTERVAL '1 hour'
   GROUP BY phase;
   ```

5. For items stuck in `SUBMITTING` phase (lease expired), force re-lease:
   ```sql
   UPDATE submit_outbox
     SET phase='PENDING', lease_expires_at=NULL, lease_holder=NULL
   WHERE phase='SUBMITTING'
     AND lease_expires_at < NOW() - INTERVAL '5 minutes';
   ```

---

## 6. RECONCILIATION_DAEMON_STOPPED — Background Daemon Not Running

**Symptom**: `reconciliation_daemon: false` in `/health/live` or `/health/deep`.

**Impact**: UNKNOWN orders are not being reconciled.  Positions may remain in
limbo state indefinitely.

**Response**:

1. Check daemon status:
   ```
   GET /health/live
   ```

2. Check recent daemon heartbeats:
   ```sql
   SELECT * FROM worker_heartbeats
   WHERE worker_name='reconciliation_daemon'
   ORDER BY updated_at DESC LIMIT 1;
   ```

3. Restart the API service.  The daemon starts automatically on startup
   if `ENABLE_RECONCILIATION_DAEMON=true` is set.

4. Verify the env var is set in production:
   ```
   GET /v1/runtime/production-boundary
   ```
   Check `reconciliation_daemon: true` under `checks`.

---

## 7. SOVEREIGN_KILL_DIRECTIVE_BLOCKED (FULL_AUTO mode)

**Symptom**: Logs show `KILL directive blocked — require_human_approval_for_kill=True`.

**Impact**: A cluster that should be killed is being demoted to THROTTLE instead.
The bot will continue trading but at reduced lot size.

**Response**:

1. Review the SovereignOversightEngine report in the latest bot snapshot:
   ```
   GET /v1/workspaces/{workspace_id}/bots/{bot_id}/snapshot
   ```
   Look for `sovereign_oversight.directives` and clusters with `KILL` directive.

2. If the kill is warranted, apply it manually via the admin kill-switch:
   ```
   POST /v1/workspaces/{workspace_id}/bots/{bot_id}/stop
   ```

3. If the kill is NOT warranted, review the `kill_threshold` in the
   SovereignPolicy config and adjust via `bot_instance_configs.strategy_config`.

4. To permanently allow autonomous kills in FULL_AUTO, set
   `require_human_approval_for_kill: false` in the bot's strategy config.
   **Only do this after thorough backtesting and ops review.**
