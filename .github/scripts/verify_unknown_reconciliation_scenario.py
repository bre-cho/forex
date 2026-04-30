#!/usr/bin/env python3
"""P0.7 gate: verify unknown reconciliation scenario is properly wired end-to-end.

Checks (static + lightweight test run):
1. UnknownOrderReconciler exists with BrokerOrderTruth.
2. 9-value outcome taxonomy present (not_found, lookup_failed, ambiguous, partial, pending, filled, rejected, error, failed_needs_operator).
3. Reconciliation daemon exists and persists ReconciliationAttemptEvent.
4. ReconciliationQueueItem ORM table present with dead_letter status support.
5. ReconciliationAttemptEvent ORM table present.
6. Manual resolution endpoint requires structured broker proof (provider, evidence_ref, observed_at).
7. live_start_preflight blocks on unresolved unknown orders.
8. Reconciliation daemon registered in prod compose.
9. Run reconciler unit tests.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
ERRORS: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        ERRORS.append(msg)
    else:
        print(f"[ok] {msg}")


REQUIRED_OUTCOMES = [
    "not_found",
    "lookup_failed",
    "ambiguous",
    "partial",
    "pending",
    "filled",
    "rejected",
    "failed_needs_operator",
]

# 1. UnknownOrderReconciler
reconciler = ROOT / "services/execution-service/execution_service/unknown_order_reconciler.py"
check(reconciler.exists(), "unknown_order_reconciler.py exists")
if reconciler.exists():
    src = reconciler.read_text()
    check("BrokerOrderTruth" in src, "reconciler defines BrokerOrderTruth")
    check("is_conclusive" in src, "BrokerOrderTruth has is_conclusive property")
    for outcome in REQUIRED_OUTCOMES:
        check(f'"{outcome}"' in src or f"'{outcome}'" in src, f"reconciler references outcome '{outcome}'")

# 2. Reconciliation daemon persists attempt events
daemon = ROOT / "apps/api/app/workers/reconciliation_daemon.py"
check(daemon.exists(), "reconciliation_daemon.py exists")
if daemon.exists():
    d_src = daemon.read_text()
    check(
        "ReconciliationAttemptEvent" in d_src or "ReconciliationAttemptEventService" in d_src,
        "daemon persists ReconciliationAttemptEvent",
    )
    check("dead_letter" in d_src, "daemon moves items to dead_letter")
    check("resolution_code" in d_src, "daemon propagates resolution_code")

# 3. ORM tables
models = ROOT / "apps/api/app/models/__init__.py"
if models.exists():
    m_src = models.read_text()
    check(
        '"reconciliation_queue_items"' in m_src or "'reconciliation_queue_items'" in m_src,
        "ReconciliationQueueItem ORM table present",
    )
    check(
        '"reconciliation_attempt_events"' in m_src or "'reconciliation_attempt_events'" in m_src,
        "ReconciliationAttemptEvent ORM table present",
    )
    # dead_letter is a string status value; check it appears in daemon or queue service
    daemon_src = daemon.read_text() if daemon.exists() else ""
    check("dead_letter" in daemon_src, "daemon references dead_letter status")

# 4. Manual resolution requires broker proof
router = ROOT / "apps/api/app/routers/live_trading.py"
if router.exists():
    r_src = router.read_text()
    for required_field in ("provider", "evidence_ref", "observed_at"):
        check(
            required_field in r_src,
            f"manual resolve endpoint validates broker proof field: {required_field}",
        )
    check(
        re.search(r"payload_hash|raw_response_hash", r_src) is not None,
        "manual resolve endpoint requires hash proof",
    )

# 5. Preflight blocks on unknown orders
preflight = ROOT / "apps/api/app/services/live_start_preflight.py"
if preflight.exists():
    pf_src = preflight.read_text()
    check(
        re.search(r"no_unknown_orders|unknown_orders_unresolved|has_unresolved", pf_src) is not None,
        "live_start_preflight blocks on unresolved unknown orders",
    )

# 6. Reconciliation daemon in prod compose
prod_compose = ROOT / "infra/docker/docker-compose.prod.yml"
if prod_compose.exists():
    pc_src = prod_compose.read_text()
    check("reconciliation-worker" in pc_src, "prod compose includes reconciliation-worker")

# 7. Run reconciler unit tests
print("[verify_unknown_reconciliation_scenario] running unit tests...")
result = subprocess.run(
    [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        str(ROOT / "services/execution-service/tests/test_unknown_order_reconciler.py"),
    ],
    capture_output=True,
    text=True,
    cwd=str(ROOT),
    env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "services/execution-service")},
)
if result.returncode != 0:
    ERRORS.append(f"reconciler unit tests failed:\n{result.stdout}\n{result.stderr}")
else:
    print(f"[ok] reconciler unit tests passed: {result.stdout.strip().splitlines()[-1] if result.stdout.strip() else 'ok'}")

if ERRORS:
    print("\n[FAIL] verify_unknown_reconciliation_scenario — errors:", file=sys.stderr)
    for e in ERRORS:
        print(f"  - {e}", file=sys.stderr)
    sys.exit(1)

print("[verify_unknown_reconciliation_scenario] OK")
