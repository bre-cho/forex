# Forex Trading Platform — Monorepo

A production-grade, multi-tenant Forex trading platform built as a Turborepo monorepo.

## Repository Structure

```
forex/
├── apps/
│   ├── api/          # FastAPI backend (Python) — REST + WebSocket API
│   ├── web/          # Next.js 14 App Router — main trader dashboard
│   └── admin/        # Next.js admin panel
├── services/
│   ├── trading-core/      # Core trading engines + BotRuntime
│   ├── execution-service/ # Broker providers (cTrader, paper, MT5, Bybit)
│   ├── analytics-service/ # Performance analytics
│   ├── signal-service/    # Signal generation + broadcasting
│   ├── notification-service/ # Email, Telegram, Discord, Webhook
│   └── billing-service/   # Stripe billing + entitlements
├── packages/
│   ├── shared-schemas/   # Shared Pydantic/TypeScript schemas
│   ├── broker-sdk/       # Broker SDK abstractions
│   ├── ui/               # Shared React component library
│   └── config/           # Shared config (ESLint, Tailwind, tsconfig)
├── infra/
│   ├── docker/           # Docker Compose files (dev + prod)
│   ├── nginx/            # Nginx reverse proxy config
│   ├── postgres/         # PostgreSQL init scripts
│   ├── redis/            # Redis config
│   └── monitoring/       # Prometheus, Grafana, Loki
├── db/
│   ├── migrations/       # Alembic migration notes
│   └── seeds/            # Seed SQL scripts
├── docs/
│   ├── architecture/     # System design docs + ADRs
│   ├── product/          # Product docs
│   └── runbooks/         # Operational runbooks
├── backend/              # Legacy monolith (kept for backward compat)
└── frontend/             # Legacy Streamlit dashboard (kept for backward compat)
```

## Prerequisites

- Node.js 20+
- pnpm 9+
- Python 3.11+
- Docker + Docker Compose
- PostgreSQL 15+
- Redis 7+

## Quick Start

```bash
# 1. Copy environment variables
cp .env.example .env
# Fill in your credentials

# 2. Start infrastructure (postgres, redis, nginx)
make docker-up

# 3. Run database migrations
make migrate

# 4. Seed with sample data
make seed

# 5. Start all apps in dev mode
make dev
```

## Services

| Service | Port | Description |
|---------|------|-------------|
| `apps/api` | 8000 | FastAPI REST + WebSocket API |
| `apps/web` | 3000 | Next.js trader dashboard |
| `apps/admin` | 3001 | Next.js admin panel |
| PostgreSQL | 5432 | Primary database |
| Redis | 6379 | Cache + pub/sub |
| Nginx | 80/443 | Reverse proxy |
| Prometheus | 9090 | Metrics |
| Grafana | 3100 | Dashboards |

## API

- REST: `http://localhost:8000/v1/`
- WebSocket: `ws://localhost:8000/ws/`
- Legacy API: `http://localhost:8000/api/` (backward compat)
- Docs: `http://localhost:8000/docs`

## Architecture

See [docs/architecture/system-overview.md](docs/architecture/system-overview.md) for the full system overview.

Key design decisions:
- **BotRuntime** replaces the global `AppState` singleton, enabling per-user bot isolation
- **RuntimeRegistry** manages multiple `BotRuntime` instances for multi-tenant operation
- **PostgreSQL** replaces SQLite for production-grade persistence
- **Redis** powers real-time pub/sub for WebSocket broadcasting

## Legacy Compatibility

The existing `backend/` and `frontend/` directories are preserved for backward compatibility.
All existing `/api/*` endpoints continue to work through the legacy router in `apps/api/app/routers/legacy.py`.

To reduce logic drift between legacy and monorepo backends, CI now enforces a drift guard:
- If a PR changes `backend/`, it must also include a related update in `apps/api/`, `services/`, `docs/adr/`, `docs/architecture/`, or `tests/`.

## Development

```bash
# Run only the API
cd apps/api && uvicorn app.main:app --reload

# Run only the web app
cd apps/web && pnpm dev

# Run tests
make test

# Run linters
make lint
```

## Deployment

```bash
# Production deployment
make docker-prod
```

See [docs/runbooks/README.md](docs/runbooks/README.md) for deployment runbooks.
