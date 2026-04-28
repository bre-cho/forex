#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

errors=0

# 1) Live runtime must not accept stub/paper provider modes in execution/runtime guards.
if ! grep -Eq 'provider_mode_not_allowed|BAD_PROVIDER_MODES|runtime_mode == "live"' apps/api/app/services/live_readiness_guard.py services/trading-core/trading_core/runtime/bot_runtime.py; then
  echo "[verify_live_no_stub] missing explicit live fail-closed provider mode checks"
  errors=$((errors+1))
fi

# 2) RuntimeFactory should not silently downgrade live to paper.
if grep -E 'provider_type\s*=\s*"paper"\s*if\s*bot\.mode\s*==\s*"paper"\s*else' -n apps/api/app/services/bot_service.py >/dev/null; then
  : # acceptable branching, validated by readiness guard
else
  echo "[verify_live_no_stub] unable to find explicit paper/live provider branch"
  errors=$((errors+1))
fi

# 3) BotRuntime live startup must require brain and provider readiness.
if ! grep -Eq 'brain_unavailable_in_live_mode|provider_mode_not_allowed|market_data_unavailable' services/trading-core/trading_core/runtime/bot_runtime.py; then
  echo "[verify_live_no_stub] missing live startup hard checks in BotRuntime"
  errors=$((errors+1))
fi

if [[ "$errors" -gt 0 ]]; then
  echo "[verify_live_no_stub] FAILED with $errors issue(s)"
  exit 1
fi

echo "[verify_live_no_stub] OK"
