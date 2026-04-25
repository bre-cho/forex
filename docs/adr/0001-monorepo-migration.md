# ADR-0001: Monorepo Migration

**Status:** Accepted
**Date:** 2024-01-01

## Context

The original Forex trading bot was a monolith:
- `backend/main.py` — 3055-line FastAPI app with a global `AppState` singleton
- `backend/engine/*.py` — 25 trading engine files
- `frontend/app.py` — Streamlit dashboard
- SQLite database

This architecture had critical limitations:
1. Single-tenant only (AppState singleton = one bot per process)
2. SQLite not suitable for production multi-user workloads
3. No separation of concerns between API, engines, and UI

## Decision

Migrate to a Turborepo monorepo with:
1. `apps/api` — FastAPI with PostgreSQL, replacing the monolith
2. `apps/web` — Next.js 14 replacing Streamlit for production UI
3. `services/trading-core` — Engine library with BotRuntime/RuntimeRegistry replacing AppState
4. Dedicated service packages for execution, analytics, signals, notifications, billing

## Consequences

### Positive
- Multi-tenant: each user gets isolated BotRuntime instances
- PostgreSQL: production-grade persistence
- Redis: real-time pub/sub for WebSocket broadcasting
- Stripe billing support
- Clear service boundaries

### Neutral
- Legacy `backend/` and `frontend/` preserved for backward compatibility
- All `/api/*` endpoints continue to work via legacy router

### Negative
- Increased infrastructure complexity (more services to run)
- Learning curve for new contributors
