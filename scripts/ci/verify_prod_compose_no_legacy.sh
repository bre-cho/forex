#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/infra/docker/docker-compose.prod.yml"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker command not found"
  exit 1
fi

services="$(docker compose -f "$COMPOSE_FILE" config --services)"

echo "[verify] services from prod compose:"
echo "$services"

for banned in backend frontend legacy streamlit; do
  if echo "$services" | grep -E "^${banned}$" >/dev/null 2>&1; then
    echo "[error] banned service found in prod compose: $banned"
    exit 1
  fi
done

for required in api web postgres redis reconciliation-worker integrity-worker; do
  if ! echo "$services" | grep -E "^${required}$" >/dev/null 2>&1; then
    echo "[error] required service missing in prod compose: $required"
    exit 1
  fi
done

echo "[ok] prod compose excludes legacy services and includes required live stack"
