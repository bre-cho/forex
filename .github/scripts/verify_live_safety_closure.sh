#!/usr/bin/env bash
# .github/scripts/verify_live_safety_closure.sh
# CI gate: verifies all mandatory live safety components are present and correct.
# Fails (exit 1) if any check fails.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ERRORS=0

# ── helper ────────────────────────────────────────────────────────────────────
fail() { echo "FAIL: $*" >&2; ERRORS=$((ERRORS + 1)); }
pass() { echo "OK:   $*"; }

# ── 1. DailyTradingState import in bot_service ────────────────────────────────
BOT_SVC="$REPO_ROOT/apps/api/app/services/bot_service.py"
if grep -q "DailyTradingState" "$BOT_SVC"; then
    pass "bot_service.py imports DailyTradingState"
else
    fail "bot_service.py missing DailyTradingState import"
fi

# ── 2. PreExecutionGate uses unified resolver ─────────────────────────────────
GATE="$REPO_ROOT/services/trading-core/trading_core/runtime/pre_execution_gate.py"
if grep -q "from trading_core.risk.daily_profit_policy import resolve_daily_take_profit_target" "$GATE"; then
    pass "pre_execution_gate uses unified daily_profit_policy resolver"
else
    fail "pre_execution_gate does not import resolve_daily_take_profit_target"
fi

# ── 3. PreExecutionGate has daily_locked check ────────────────────────────────
if grep -q "daily_locked" "$GATE"; then
    pass "pre_execution_gate has daily_locked explicit check"
else
    fail "pre_execution_gate missing daily_locked check"
fi

# ── 4. daily_profit_policy.py resolver present ───────────────────────────────
RESOLVER="$REPO_ROOT/services/trading-core/trading_core/risk/daily_profit_policy.py"
if [ -f "$RESOLVER" ]; then
    pass "daily_profit_policy.py resolver exists"
else
    fail "daily_profit_policy.py resolver missing"
fi

# ── 5. live_start_preflight syncs broker equity ───────────────────────────────
PREFLIGHT="$REPO_ROOT/apps/api/app/services/live_start_preflight.py"
if grep -q "recompute_from_broker_equity" "$PREFLIGHT"; then
    pass "live_start_preflight syncs broker equity before daily freshness check"
else
    fail "live_start_preflight missing recompute_from_broker_equity call"
fi

# ── 6. live_start_preflight blocks active daily lock ─────────────────────────
if grep -q "daily_lock_active" "$PREFLIGHT"; then
    pass "live_start_preflight blocks if daily lock active"
else
    fail "live_start_preflight missing daily_lock_active block"
fi

# ── 7. OrderProjectionService exists ─────────────────────────────────────────
PROJ_SVC="$REPO_ROOT/apps/api/app/services/order_projection_service.py"
if [ -f "$PROJ_SVC" ]; then
    pass "OrderProjectionService exists"
else
    fail "OrderProjectionService missing"
fi

# ── 8. OrderProjectionService has required methods ───────────────────────────
for METHOD in "upsert_from_order_attempt" "upsert_from_execution_receipt" "sync_order_status_from_state_transition"; do
    if grep -q "$METHOD" "$PROJ_SVC"; then
        pass "OrderProjectionService.$METHOD present"
    else
        fail "OrderProjectionService missing method: $METHOD"
    fi
done

# ── 9. UnknownOrderReconciler exists ─────────────────────────────────────────
UOR="$REPO_ROOT/services/execution-service/execution_service/unknown_order_reconciler.py"
if [ -f "$UOR" ]; then
    pass "UnknownOrderReconciler exists"
else
    fail "UnknownOrderReconciler missing"
fi

# ── 10. UnknownOrderReconciler has FAILED_NEEDS_OPERATOR path ────────────────
if grep -q "failed_needs_operator" "$UOR"; then
    pass "UnknownOrderReconciler has failed_needs_operator escalation"
else
    fail "UnknownOrderReconciler missing failed_needs_operator escalation"
fi

# ── 11. Python AST compile check for new files ────────────────────────────────
for PYFILE in "$BOT_SVC" "$GATE" "$RESOLVER" "$PREFLIGHT" "$PROJ_SVC" "$UOR"; do
    if python3 -c "import ast; ast.parse(open('$PYFILE').read())" 2>/dev/null; then
        pass "AST OK: $(basename $PYFILE)"
    else
        fail "AST FAIL: $(basename $PYFILE)"
    fi
done

# ── 12. Run unknown_order_reconciler tests ───────────────────────────────────
EXEC_SVC="$REPO_ROOT/services/execution-service"
if [ -d "$EXEC_SVC" ]; then
    echo "--- Running UnknownOrderReconciler tests ---"
    if PYTHONPATH="$EXEC_SVC:$REPO_ROOT/services/trading-core" \
        python3 -m pytest "$EXEC_SVC/tests/test_unknown_order_reconciler.py" -q --tb=short 2>&1; then
        pass "UnknownOrderReconciler tests passed"
    else
        fail "UnknownOrderReconciler tests failed"
    fi
else
    fail "execution-service directory not found"
fi

# ── 13. Manual signal route must use same live preflight gate ───────────────
BOTS_ROUTER="$REPO_ROOT/apps/api/app/routers/bots.py"
if grep -q "@router.post(\"/{bot_id}/manual-signal\")" "$BOTS_ROUTER"; then
    pass "bots manual-signal route exists"
else
    fail "manual-signal route missing"
fi

if grep -q "manual_signal_blocked" "$BOTS_ROUTER"; then
    pass "manual-signal route has explicit live block reason"
else
    fail "manual-signal route missing live block reason"
fi

if grep -q "run_live_start_preflight" "$BOTS_ROUTER"; then
    pass "manual-signal route uses run_live_start_preflight"
else
    fail "manual-signal route does not call run_live_start_preflight"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
if [ "$ERRORS" -gt 0 ]; then
    echo "LIVE SAFETY CLOSURE: $ERRORS check(s) FAILED" >&2
    exit 1
else
    echo "LIVE SAFETY CLOSURE: all checks passed"
fi
