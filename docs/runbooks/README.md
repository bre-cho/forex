# Operational Runbooks

## Runbook Index

- [Integrity Worker Runbook](integrity-worker.md)

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

## Integrity Worker

```bash
# Worker logs
docker compose -f infra/docker/docker-compose.prod.yml logs -f integrity-worker

# Run one-shot integrity check inside API container
docker compose -f infra/docker/docker-compose.prod.yml exec api \
	python -m app.workers.verify_order_ledger_integrity
```

Chi tiết biến môi trường, override lịch và xử lý incident:

- [Integrity Worker Runbook](integrity-worker.md)

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
