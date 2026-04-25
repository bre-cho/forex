# Operational Runbooks

## Starting the Platform

```bash
# Development
make docker-up
make migrate
make dev

# Production
docker compose -f infra/docker/docker-compose.prod.yml up -d
cd apps/api && alembic upgrade head
```

## Database Migrations

```bash
# Generate migration from model changes
cd apps/api && alembic revision --autogenerate -m "add_feature"

# Apply
alembic upgrade head

# Rollback
alembic downgrade -1
```

## Monitoring

- Prometheus: http://localhost:9090
- Grafana: http://localhost:3100 (admin/admin)
- API logs: `docker compose logs api -f`

## Incident Response

### Bot stuck in STARTING state
```bash
# Via admin API
curl -X POST http://localhost:8000/v1/admin/runtime/stop-all
```

### Database connection issues
```bash
docker compose ps postgres
docker compose restart postgres
```

### Redis connection issues
```bash
docker compose ps redis
docker compose restart redis
```
