#!/usr/bin/env bash
set -euo pipefail

targets=(
  "infra/docker/docker-compose.prod.yml"
  ".github/workflows/release.yml"
)

patterns=(
  "backend.main"
  "backend/main.py"
  "frontend/app.py"
  "./backend"
  "./frontend"
)

violations=""
for target in "${targets[@]}"; do
  if [[ ! -f "$target" ]]; then
    continue
  fi
  for pattern in "${patterns[@]}"; do
    if grep -nF "$pattern" "$target" >/dev/null 2>&1; then
      violations+="$target contains forbidden legacy reference: $pattern\n"
    fi
  done
done

if [[ -n "$violations" ]]; then
  echo "FAIL: production config references legacy backend/frontend stack"
  printf "%b" "$violations"
  exit 1
fi

echo "OK: production config is isolated from legacy backend/frontend stack"