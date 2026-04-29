#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ERRORS=0

fail() { echo "FAIL: $*" >&2; ERRORS=$((ERRORS + 1)); }
pass() { echo "OK:   $*"; }

# ── 1. Production compose must not reference legacy stack ─────────────────────
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

for target in "${targets[@]}"; do
  if [[ ! -f "$REPO_ROOT/$target" ]]; then
    continue
  fi
  for pattern in "${patterns[@]}"; do
    if grep -nF "$pattern" "$REPO_ROOT/$target" >/dev/null 2>&1; then
      fail "$target contains forbidden legacy reference: $pattern"
    fi
  done
done
pass "production compose/release has no legacy stack references"

# ── 2. Production Dockerfiles must not COPY backend/ or frontend/ ────────────
PROD_DOCKERFILES=(
  "apps/api/Dockerfile"
  "services/trading-core/Dockerfile"
  "services/execution-service/Dockerfile"
)
for df in "${PROD_DOCKERFILES[@]}"; do
  if [[ ! -f "$REPO_ROOT/$df" ]]; then
    continue
  fi
  for legacy_dir in "backend" "frontend"; do
    if grep -E "^COPY.* $legacy_dir" "$REPO_ROOT/$df" >/dev/null 2>&1 || \
       grep -E "^ADD.* $legacy_dir" "$REPO_ROOT/$df" >/dev/null 2>&1; then
      fail "$df copies legacy $legacy_dir/ directory"
    fi
  done
done
pass "production Dockerfiles do not copy legacy directories"

# ── 3. apps/api must not import from backend/ ─────────────────────────────────
if grep -rn "from backend\." "$REPO_ROOT/apps/api/" 2>/dev/null | grep -v "__pycache__" | grep -v ".pyc" | head -5; then
  fail "apps/api imports from legacy backend/"
else
  pass "apps/api has no imports from legacy backend/"
fi

# ── 4. services/* must not import from backend/ ──────────────────────────────
for svc_dir in "$REPO_ROOT/services"/*/; do
  svc_name=$(basename "$svc_dir")
  if grep -rn "from backend\." "$svc_dir" 2>/dev/null | grep -v "__pycache__" | grep -v ".pyc" | head -1; then
    fail "services/$svc_name imports from legacy backend/"
  fi
done
pass "services/* have no imports from legacy backend/"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
if [ "$ERRORS" -gt 0 ]; then
  echo "PRODUCTION NO-LEGACY: $ERRORS check(s) FAILED" >&2
  exit 1
else
  echo "PRODUCTION NO-LEGACY: all checks passed"
fi


echo "OK: production config is isolated from legacy backend/frontend stack"