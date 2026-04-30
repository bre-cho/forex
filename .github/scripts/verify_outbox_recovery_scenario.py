#!/usr/bin/env python3
"""P0.7 gate: verify outbox recovery scenario is properly wired.

Checks (static, no DB required):
1. submit_outbox_recovery_worker.py exists and imports SubmitOutboxService.
2. submit_outbox_recovery_worker_entrypoint.py exists and calls run_submit_outbox_recovery_worker.
3. SubmitOutboxRecoveryHealthService exists and is imported by live_start_preflight.
4. SubmitOutbox ORM table present in models/__init__.py.
5. Prod compose includes integrity-worker service (which covers outbox recovery worker).
6. No silent broad except that could swallow outbox errors in the recovery loop.
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


# 1. Recovery worker exists and imports SubmitOutboxService
worker = ROOT / "apps/api/app/workers/submit_outbox_recovery_worker.py"
check(worker.exists(), "submit_outbox_recovery_worker.py exists")
if worker.exists():
    src = worker.read_text()
    check("SubmitOutboxService" in src, "recovery worker imports SubmitOutboxService")
    check("run_submit_outbox_recovery_worker" in src, "recovery worker exposes run_submit_outbox_recovery_worker")
    check(
        re.search(r"dead_letter|stale|SUBMITTING|unknown_queue", src) is not None,
        "recovery worker handles stale/SUBMITTING outbox items",
    )

# 2. Entrypoint exists
entrypoint = ROOT / "apps/api/app/workers/submit_outbox_recovery_worker_entrypoint.py"
check(entrypoint.exists(), "submit_outbox_recovery_worker_entrypoint.py exists")
if entrypoint.exists():
    src_ep = entrypoint.read_text()
    check("run_submit_outbox_recovery_worker" in src_ep, "entrypoint calls run_submit_outbox_recovery_worker")

# 3. SubmitOutboxRecoveryHealthService wired into preflight
health_svc = ROOT / "apps/api/app/services/submit_outbox_recovery_health_service.py"
check(health_svc.exists(), "submit_outbox_recovery_health_service.py exists")
preflight = ROOT / "apps/api/app/services/live_start_preflight.py"
if preflight.exists():
    pf_src = preflight.read_text()
    check(
        "SubmitOutboxRecoveryHealthService" in pf_src,
        "live_start_preflight imports SubmitOutboxRecoveryHealthService",
    )
    check(
        "submit_outbox_recovery_healthy" in pf_src,
        "live_start_preflight checks submit_outbox_recovery_healthy",
    )

# 4. SubmitOutbox ORM table present
models = ROOT / "apps/api/app/models/__init__.py"
if models.exists():
    m_src = models.read_text()
    check('"submit_outbox"' in m_src or "'submit_outbox'" in m_src, "SubmitOutbox ORM table present in models")
    check(
        '"submit_outbox_events"' in m_src or "'submit_outbox_events'" in m_src,
        "SubmitOutboxEvent ORM table present in models",
    )

# 5. Prod compose has integrity-worker (runs outbox recovery worker)
prod_compose = ROOT / "infra/docker/docker-compose.prod.yml"
if prod_compose.exists():
    pc_src = prod_compose.read_text()
    check("integrity-worker" in pc_src, "prod compose includes integrity-worker")

# 6. No silent bare 'except Exception: pass' without logging in recovery worker
if worker.exists():
    src = worker.read_text()
    # Allow 'except Exception as ...: logger.warning(...)' but not bare silent pass
    bare_silent = re.findall(r"except\s+Exception\s*:\s*\n\s*pass", src)
    check(len(bare_silent) == 0, "recovery worker has no silent bare 'except Exception: pass' (bare pass without logging)")

if ERRORS:
    print("\n[FAIL] verify_outbox_recovery_scenario — errors:", file=sys.stderr)
    for e in ERRORS:
        print(f"  - {e}", file=sys.stderr)
    sys.exit(1)

print("[verify_outbox_recovery_scenario] OK")
