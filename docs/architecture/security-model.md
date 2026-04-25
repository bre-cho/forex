# Security Model

## Authentication

- JWT Bearer tokens (HS256)
- Access token: 30 minutes
- Refresh token: 30 days
- Tokens stored in localStorage (web) — consider httpOnly cookies for production

## Authorization (RBAC)

Workspace roles:
| Role | Permissions |
|------|-------------|
| owner | Full control, billing, delete workspace |
| admin | Manage bots, brokers, members |
| trader | Start/stop bots, view signals |
| viewer | Read-only access |

## Secrets

- All secrets in `.env` (never committed)
- Broker credentials stored in `broker_connections.credentials` (encrypt at rest in production)
- Stripe webhook secret verified on every webhook call

## API Security

- CORS configured per environment
- Rate limiting via Redis sliding window (60 req/min per IP)
- Request ID on every request for tracing
- Sentry error tracking in production
