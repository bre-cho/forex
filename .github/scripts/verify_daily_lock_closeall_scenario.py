#!/usr/bin/env python3
"""P0.7 gate: verify daily lock close-all scenario is properly wired.

Checks (static, no DB required):
1. DailyLockRuntimeController exists with close_all_and_stop action.
2. _close_all_positions method present.
3. Postcondition check present (raises/records incident on partial close).
4. DailyLockAction ORM model present.
5. live_start_preflight blocks if daily lock active.
6. daily_profit_policy.py resolver is imported by pre_execution_gate.
7. daily_locked explicit check in pre_execution_gate.
8. Manual reset endpoint present in live_trading router.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
ERRORS: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        ERRORS.append(msg)
    else:
        print(f"[ok] {msg}")


# 1. DailyLockRuntimeController
controller = ROOT / "apps/api/app/services/daily_lock_runtime_controller.py"
check(controller.exists(), "daily_lock_runtime_controller.py exists")
if controller.exists():
    src = controller.read_text()
    check("close_all_and_stop" in src, "controller handles close_all_and_stop action")
    check("_close_all_positions" in src, "controller has _close_all_positions method")
    # Postcondition: raises or creates incident when close incomplete
    check(
        "close_all_postcondition_failed" in src or "close_all_positions_incomplete" in src,
        "controller has postcondition failure path for close_all",
    )
    check(
        re.search(r"incident_type.*close_all", src) is not None,
        "controller creates incident on close_all failure",
    )

# 2. DailyLockAction ORM
models = ROOT / "apps/api/app/models/__init__.py"
if models.exists():
    m_src = models.read_text()
    check('"daily_lock_actions"' in m_src or "'daily_lock_actions'" in m_src, "DailyLockAction ORM table present")
    check("close_all_and_stop" in m_src, "DailyLockAction model includes close_all_and_stop value")

# 3. Preflight blocks on daily lock
preflight = ROOT / "apps/api/app/services/live_start_preflight.py"
if preflight.exists():
    pf_src = preflight.read_text()
    check(
        re.search(r"daily_lock|daily_locked", pf_src) is not None,
        "live_start_preflight checks for active daily lock",
    )

# 4. Pre-execution gate enforces daily_locked
gate = ROOT / "services/trading-core/trading_core/pre_execution_gate.py"
if gate.exists():
    g_src = gate.read_text()
    check("daily_locked" in g_src, "pre_execution_gate has explicit daily_locked check")
    check(
        re.search(r"daily_profit_policy|DailyProfitPolicy", g_src) is not None,
        "pre_execution_gate imports daily_profit_policy resolver",
    )

# 5. Manual reset endpoint in live_trading router
router = ROOT / "apps/api/app/routers/live_trading.py"
if router.exists():
    r_src = router.read_text()
    check(
        re.search(r"reset.*daily.*lock|daily.*lock.*reset", r_src, re.I) is not None,
        "live_trading router has daily lock reset endpoint",
    )
    check(
        "daily_lock" in r_src,
        "live_trading router references daily_lock actions",
    )

# 6. close_all_positions provider method check in controller
if controller.exists():
    src = controller.read_text()
    check(
        "close_all_positions" in src,
        "controller calls close_all_positions on provider",
    )

if ERRORS:
    print("\n[FAIL] verify_daily_lock_closeall_scenario — errors:", file=sys.stderr)
    for e in ERRORS:
        print(f"  - {e}", file=sys.stderr)
    sys.exit(1)

print("[verify_daily_lock_closeall_scenario] OK")
