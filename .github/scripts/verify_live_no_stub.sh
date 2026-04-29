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

# 1.1) MT5/Bybit providers must not be hardcoded as stub names in readiness classification.
if grep -E 'mt5provider|bybitprovider' -n apps/api/app/services/bot_service.py >/dev/null; then
  echo "[verify_live_no_stub] bot_service still classifies MT5/Bybit provider class names as stub"
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

# 4) Reconciliation import failure in live path must block startup, not warning-only fallback.
if ! grep -Eq 'reconciliation_worker_unavailable' services/trading-core/trading_core/runtime/bot_runtime.py; then
  echo "[verify_live_no_stub] missing hard-fail on reconciliation import failure"
  errors=$((errors+1))
fi

# 5) cTrader live path must fail closed when execution adapter is unavailable.
if ! grep -Eq 'live execution adapter unavailable|_execution_adapter\.available' services/execution-service/execution_service/providers/ctrader.py; then
  echo "[verify_live_no_stub] missing cTrader live execution-adapter fail-closed checks"
  errors=$((errors+1))
fi

# 6) Idempotency unique constraint must exist in model or migration.
if ! grep -Eq 'uq_order_idempotency_bot_key|UniqueConstraint\("bot_instance_id", "idempotency_key"' apps/api/app/models/__init__.py apps/api/alembic/versions/0004_order_idempotency_reservations.py; then
  echo "[verify_live_no_stub] missing idempotency unique constraint"
  errors=$((errors+1))
fi

# 7) Daily TP/loss tests must be present.
if [[ ! -f apps/api/tests/test_daily_trading_state.py ]]; then
  echo "[verify_live_no_stub] missing daily trading state test"
  errors=$((errors+1))
fi

if [[ "$errors" -gt 0 ]]; then
  echo "[verify_live_no_stub] FAILED with $errors issue(s)"
  exit 1
fi

echo "[verify_live_no_stub] OK"
