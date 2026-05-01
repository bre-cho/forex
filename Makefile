.PHONY: dev build test test-api test-services install-test lint migrate seed docker-up docker-down docker-prod

# ── Development ──────────────────────────────────────────────────────────────
dev:
	pnpm turbo dev --parallel

build:
	pnpm turbo build

test:
	pytest apps/api/tests services/trading-core/tests services/execution-service/tests -q

test-api:
	pytest apps/api/tests -q

test-services:
	pytest services/trading-core/tests services/execution-service/tests -q

lint:
	pnpm turbo lint

# ── Database ─────────────────────────────────────────────────────────────────
migrate:
	cd apps/api && alembic upgrade head

seed:
	psql "$$DATABASE_URL" -f db/seeds/001_initial_seed.sql

# ── Docker helpers ───────────────────────────────────────────────────────────
docker-up:
	docker compose -f infra/docker/docker-compose.dev.yml up -d

docker-down:
	docker compose -f infra/docker/docker-compose.dev.yml down

docker-prod:
	docker compose -f infra/docker/docker-compose.prod.yml up -d

# ── Python services (via uv/pip) ─────────────────────────────────────────────
install-python:
	cd services/trading-core && pip install -e ".[dev]"
	cd services/execution-service && pip install -e ".[dev]"
	cd services/analytics-service && pip install -e ".[dev]"
	cd services/signal-service && pip install -e ".[dev]"
	cd services/notification-service && pip install -e ".[dev]"
	cd services/billing-service && pip install -e ".[dev]"
	cd apps/api && pip install -e ".[dev]"

install-test:
	pip install -r requirements-test.txt
	pip install -e apps/api
	pip install -e services/trading-core

# ── Shorthand ────────────────────────────────────────────────────────────────
up: docker-up
down: docker-down
