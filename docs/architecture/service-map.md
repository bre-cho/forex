# Service Dependency Map

```
apps/web ──────────────────────────────────► apps/api
apps/admin ─────────────────────────────────► apps/api
apps/api ──────────────────────────────────► PostgreSQL
apps/api ──────────────────────────────────► Redis
apps/api ──────────────────────────────────► services/trading-core
apps/api ──────────────────────────────────► services/execution-service
apps/api ──────────────────────────────────► services/notification-service
apps/api ──────────────────────────────────► services/billing-service
services/trading-core ─────────────────────► services/execution-service
services/signal-service ───────────────────► Redis (pub/sub)
services/analytics-service ────────────────► PostgreSQL (read queries)
```

## Dependency Rules

- `apps/api` is the **only** service that writes to PostgreSQL
- All other services communicate with `apps/api` via internal API calls or shared packages
- Redis is used for pub/sub (WebSocket broadcasting) and cache only
- No service imports directly from `backend/` (legacy code)
