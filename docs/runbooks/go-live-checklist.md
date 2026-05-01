# Go-Live Checklist — Forex Trading Platform

> **Purpose:** Authoritative pre-production checklist. Every item must be ✅
> before starting a live bot with real money. Print this document, work
> through it with a second person ("four-eyes"), and sign off each section.

---

## 0. Prerequisites

| # | Check | Owner | Done |
|---|-------|-------|------|
| 0.1 | Staging/paper trading ran for ≥ 5 days without incidents | Ops | ☐ |
| 0.2 | All automated tests pass (`make test`) | Dev | ☐ |
| 0.3 | All linters pass (`make lint`) | Dev | ☐ |
| 0.4 | Security audit completed (no CRITICAL CVEs in deps) | Dev | ☐ |
| 0.5 | PostgreSQL backup verified (restore tested on staging) | Ops | ☐ |
| 0.6 | Monitoring dashboards visible in Grafana | Ops | ☐ |

---

## 1. Environment & Secrets

### 1.1 Application Environment

```bash
APP_ENV=production
IS_PRODUCTION=true
DEBUG=false
```

Set in `.env` **and** in the Docker Compose service definition.

| # | Check | Done |
|---|-------|------|
| 1.1 | `APP_ENV=production` | ☐ |
| 1.2 | `IS_PRODUCTION=true` | ☐ |
| 1.3 | `DEBUG=false` | ☐ |
| 1.4 | `ENABLE_LEGACY_ROUTES=false` | ☐ |

### 1.2 Production Safety Gates

| # | Variable | Required Value | Done |
|---|----------|---------------|------|
| 1.5 | `ALLOW_STUB_RUNTIME` | `false` | ☐ |
| 1.6 | `REQUIRE_SIGNED_GATE_CONTEXT` | `true` | ☐ |
| 1.7 | `LIVE_BURN_IN_REQUIRED` | `true` | ☐ |
| 1.8 | `ENABLE_RECONCILIATION_DAEMON` | `true` | ☐ |
| 1.9 | `ENABLE_SUBMIT_OUTBOX_RECOVERY_WORKER` | `true` | ☐ |
| 1.10 | `LIVE_CERT_MAX_AGE_HOURS` | `168` (7 days) | ☐ |

### 1.3 Secrets (minimum 32 random characters each)

Generate fresh secrets for production — **never reuse dev/staging values**:

```bash
# SECRET_KEY
python -c "import secrets; print(secrets.token_hex(32))"

# JWT_SECRET
python -c "import secrets; print(secrets.token_hex(32))"

# FROZEN_CONTEXT_HMAC_SECRET
python -c "import secrets; print(secrets.token_hex(32))"

# FERNET_KEY (for broker credential encryption)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

| # | Secret | Done |
|---|--------|------|
| 1.11 | `SECRET_KEY` set (≥ 64 hex chars) | ☐ |
| 1.12 | `JWT_SECRET` set (≥ 64 hex chars) | ☐ |
| 1.13 | `FROZEN_CONTEXT_HMAC_SECRET` set (≥ 64 hex chars) | ☐ |
| 1.14 | `FERNET_KEY` set (Fernet-generated) | ☐ |
| 1.15 | Secrets stored in Docker Secrets or secret manager (not plain `.env`) | ☐ |

---

## 2. Database

| # | Check | Command | Done |
|---|-------|---------|------|
| 2.1 | Alembic migration applied | `cd apps/api && alembic upgrade head` | ☐ |
| 2.2 | All 15 tables exist | `\dt` in psql | ☐ |
| 2.3 | PgBouncer health | `pg_isready -h pgbouncer -p 5432` | ☐ |
| 2.4 | Connection pool can handle 200 concurrent clients | Load test | ☐ |
| 2.5 | PostgreSQL WAL archiving / backup configured | Ops | ☐ |
| 2.6 | Backup restore tested within last 7 days | Ops | ☐ |

---

## 3. Broker Credentials & Provider Certification

For **each** live broker connection (cTrader / Bybit / MT5Bridge):

### 3.1 Credential Verification

| # | Check | Done |
|---|-------|------|
| 3.1 | Broker credentials stored with Fernet encryption in DB | ☐ |
| 3.2 | `credential_scope=live` for live connections | ☐ |
| 3.3 | OAuth2 token not expired (cTrader) | ☐ |
| 3.4 | API key has correct permissions (Bybit) | ☐ |
| 3.5 | MT5Bridge service running and accessible (MT5) | ☐ |

### 3.2 Provider Certification (11 required checks)

Run the certification script for each provider and verify all pass:

```bash
# cTrader
python scripts/live_cert/ctrader_demo_cert.py

# Bybit
python scripts/live_cert/bybit_testnet_cert.py

# MT5 Bridge
python scripts/live_cert/mt5_bridge_cert.py
```

**Required checks** — all must be `PASS`:

| Check | Status |
|-------|--------|
| `account_authorized` | ☐ PASS |
| `account_id_match` | ☐ PASS |
| `quote_realtime` | ☐ PASS |
| `server_time_valid` | ☐ PASS |
| `instrument_spec_valid` | ☐ PASS |
| `margin_estimate_valid` | ☐ PASS |
| `client_order_id_supported` | ☐ PASS |
| `order_lookup_supported` | ☐ PASS |
| `execution_lookup_supported` | ☐ PASS |
| `close_all_supported` | ☐ PASS |
| `reconciliation_roundtrip_passed` | ☐ PASS |

**Certification recorded in DB** (via `ProviderCertificationService`): ☐

---

## 4. Workers & Daemons

| # | Service | Check | Done |
|---|---------|-------|------|
| 4.1 | `reconciliation-worker` | Running, healthcheck GREEN | ☐ |
| 4.2 | `submit-outbox-recovery` | Running, healthcheck GREEN | ☐ |
| 4.3 | `integrity-worker` | Running, healthcheck GREEN | ☐ |
| 4.4 | Reconciliation queue depth = 0 | `reconciliation_queue_depth == 0` in Prometheus | ☐ |
| 4.5 | No UNKNOWN orders in DB | `SELECT count(*) FROM order_outbox WHERE status='UNKNOWN'` | ☐ |

---

## 5. Kill Switch & Daily State

| # | Check | Command | Done |
|---|-------|---------|------|
| 5.1 | System kill switch = OFF | `GET /v1/admin/kill-switch` → `active=false` | ☐ |
| 5.2 | No active daily lock for target bot | `GET /v1/bots/{id}/daily-lock` → `locked=false` | ☐ |
| 5.3 | Daily state freshly seeded for today | Confirm `daily_start_equity > 0` | ☐ |
| 5.4 | `max_daily_loss_pct` configured | e.g. `2.0` | ☐ |
| 5.5 | `max_daily_profit_pct` configured | e.g. `5.0` | ☐ |

---

## 6. AI / LLM Configuration

| # | Check | Done |
|---|-------|------|
| 6.1 | If LLM is in the critical trading path: `OPENAI_API_KEY` or `GEMINI_API_KEY` set | ☐ |
| 6.2 | LLM stub mode is NOT active in live mode | Check logs on startup | ☐ |
| 6.3 | LLM governance policy (max lot scale, kill directives) configured | ☐ |

---

## 7. Monitoring & Alerting

| # | Check | Done |
|---|-------|------|
| 7.1 | Prometheus scraping API metrics | `{job="forex-api"}` in targets | ☐ |
| 7.2 | Grafana dashboards loaded | Open Grafana → Trading Operations | ☐ |
| 7.3 | AlertManager configured (email/Telegram/Discord) | Send test alert | ☐ |
| 7.4 | `BotRuntimeHeartbeatStale` alert fires correctly (test with killed bot) | ☐ |
| 7.5 | `AccountEquityDriftHigh` alert fires correctly | ☐ |
| 7.6 | `UnknownOrderSLABreach` alert fires correctly | ☐ |
| 7.7 | Sentry DSN configured and receiving events | `SENTRY_DSN` set | ☐ |

---

## 8. Network & Security

| # | Check | Done |
|---|-------|------|
| 8.1 | TLS certificate valid and auto-renewing (Let's Encrypt) | `curl -I https://yourdomain.com` | ☐ |
| 8.2 | Nginx rate limiting enabled for `/v1/auth/*` | Config review | ☐ |
| 8.3 | MT5 Bridge bound to private network / behind VPN | Network audit | ☐ |
| 8.4 | `CORS_ORIGINS` set to production frontend URL only | ☐ |
| 8.5 | PostgreSQL not exposed to public internet | `netstat`/security group | ☐ |
| 8.6 | Redis `requirepass` enabled | `redis-cli -a $REDIS_PASSWORD ping` | ☐ |

---

## 9. Burn-In Validation (2–5 days)

Run with **live broker connection + paper mode** before switching to real execution:

| # | Check | Done |
|---|-------|------|
| 9.1 | Bot running in paper mode with live cTrader/Bybit/MT5 connection for ≥ 2 days | ☐ |
| 9.2 | All signal, order, trade lifecycle hooks fired correctly | ☐ |
| 9.3 | Daily lock triggered and cleared correctly | ☐ |
| 9.4 | Reconciliation daemon ran without incidents | ☐ |
| 9.5 | Audit log entries complete (signal → order → trade → daily state) | ☐ |
| 9.6 | Equity drift < 0.1% over burn-in period | ☐ |
| 9.7 | No CRITICAL alerts fired unexpectedly | ☐ |

---

## 10. First Live Start (Go/No-Go)

Go/No-Go decision: all sections 0–9 must have every item checked ✅.

| # | Decision | Owner | Sign-off |
|---|----------|-------|----------|
| 10.1 | All 9 sections above fully checked | Dev + Ops | ___________ |
| 10.2 | Incident response plan in place | Ops | ___________ |
| 10.3 | Kill switch accessible on mobile | Trader | ___________ |
| 10.4 | **GO** — start bot in live mode | Owner | ___________ |

### First Live Start Commands

```bash
# 1. Verify production safety flags are set
curl -sf http://localhost:8000/health | jq .

# 2. Run live start preflight via API
curl -X POST http://localhost:8000/v1/bots/{bot_id}/start \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"reason": "Initial production live start — all pre-flight checks passed"}'

# 3. Monitor immediately after start
# - Watch Grafana: Trading Operations dashboard
# - Watch logs: docker logs -f forex-api
# - Keep kill switch URL ready
```

---

## 11. Post-Launch (First 24 Hours)

| # | Check | Every | Done |
|---|-------|-------|------|
| 11.1 | Review Grafana for anomalies | 1h | ☐ |
| 11.2 | Verify P&L matches broker statement | 4h | ☐ |
| 11.3 | Check reconciliation queue depth = 0 | 1h | ☐ |
| 11.4 | Verify daily lock cleared at day start | Daily | ☐ |
| 11.5 | Backup PostgreSQL after first trading day | Daily | ☐ |

---

## Emergency Stop Procedure

```bash
# Option 1: Kill switch via API (recommended — graceful)
curl -X POST http://localhost:8000/v1/admin/kill-switch/activate \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"reason": "Emergency stop"}'

# Option 2: Stop bot via API
curl -X POST http://localhost:8000/v1/bots/{bot_id}/stop \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# Option 3: Emergency close all positions (broker-side)
curl -X POST http://localhost:8000/v1/bots/{bot_id}/close-all-positions \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# Option 4: Nuclear — stop all containers
docker compose -f infra/docker/docker-compose.prod.yml down
```

> ⚠️ **WARNING**: Stopping containers while positions are open does NOT close
> broker-side positions. Always use Option 1 or 2 first, wait for confirmation,
> then stop containers. If containers are forcibly stopped with open positions,
> manually close them in the broker terminal immediately.

---

## Fernet Key Rotation Procedure

The platform supports dual Fernet keys (`FERNET_KEY` + `FERNET_KEY_OLD`) for
zero-downtime rotation:

```bash
# 1. Generate new key
NEW_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")

# 2. Update .env: set FERNET_KEY_OLD = current FERNET_KEY value, FERNET_KEY = $NEW_KEY

# 3. Rolling restart API replicas (one at a time)
docker service update --update-parallelism 1 forex_api

# 4. After all replicas are on new key: re-encrypt credentials
# (run the key rotation admin script — see docs/runbooks/key-rotation.md)

# 5. Remove FERNET_KEY_OLD once all credentials are re-encrypted
```
