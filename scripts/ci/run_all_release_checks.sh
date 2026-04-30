#!/usr/bin/env bash
# scripts/ci/run_all_release_checks.sh
# P0.7 master release gate — run locally before tagging a release.
#
# Usage:
#   bash scripts/ci/run_all_release_checks.sh
#
# Exit code: 0 = all checks passed, 1 = one or more checks failed.
# Each check is independent; all are run even if earlier ones fail,
# so you get a full failure report in one pass.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

PASS=0
FAIL=0
FAILED_CHECKS=()

run_check() {
  local name="$1"
  shift
  echo ""
  echo "┌─ $name"
  if "$@"; then
    echo "└─ [PASS] $name"
    PASS=$((PASS + 1))
  else
    echo "└─ [FAIL] $name"
    FAIL=$((FAIL + 1))
    FAILED_CHECKS+=("$name")
  fi
}

# ── P0.7 static checks ────────────────────────────────────────────────────────

run_check "alembic_single_head" \
  python .github/scripts/verify_alembic_single_head.py

run_check "experiment_stage_policy" \
  python .github/scripts/verify_experiment_stage_policy.py

run_check "live_import_boundary" \
  python .github/scripts/verify_live_import_boundary.py

run_check "no_live_stub_provider" \
  python .github/scripts/verify_no_live_stub_provider.py

run_check "live_no_fallback_spec" \
  bash .github/scripts/verify_live_no_fallback_spec.sh

run_check "live_no_stub" \
  bash .github/scripts/verify_live_no_stub.sh

run_check "broker_gate_wiring" \
  python .github/scripts/verify_broker_gate_wiring.py

run_check "runtime_snapshot_payload" \
  python .github/scripts/verify_runtime_snapshot_payload.py

run_check "frontend_api_contract" \
  python .github/scripts/verify_frontend_api_contract.py

run_check "production_no_legacy_stack" \
  bash .github/scripts/verify_production_no_legacy_stack.sh

run_check "live_safety_closure" \
  bash .github/scripts/verify_live_safety_closure.sh

# ── P0.7 scenario checks ──────────────────────────────────────────────────────

run_check "outbox_recovery_scenario" \
  python .github/scripts/verify_outbox_recovery_scenario.py

run_check "daily_lock_closeall_scenario" \
  python .github/scripts/verify_daily_lock_closeall_scenario.py

run_check "unknown_reconciliation_scenario" \
  python .github/scripts/verify_unknown_reconciliation_scenario.py

# ── Market data quality (runs pytest) ────────────────────────────────────────

run_check "market_data_quality_scenarios" \
  bash .github/scripts/verify_market_data_quality_scenarios.sh

# ── Summary ───────────────────────────────────────────────────────────────────

echo ""
echo "════════════════════════════════════════"
echo "  P0.7 Release Gate Summary"
echo "  PASS: $PASS   FAIL: $FAIL"
echo "════════════════════════════════════════"

if [[ $FAIL -gt 0 ]]; then
  echo ""
  echo "Failed checks:"
  for c in "${FAILED_CHECKS[@]}"; do
    echo "  ✗ $c"
  done
  echo ""
  echo "Release BLOCKED. Fix all failures before tagging."
  exit 1
fi

echo ""
echo "All $PASS checks passed. Release gate OPEN."
exit 0
