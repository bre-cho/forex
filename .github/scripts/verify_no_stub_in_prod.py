#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
ERRORS: list[str] = []


def check(cond: bool, msg: str) -> None:
    if cond:
        print(f"[ok] {msg}")
    else:
        ERRORS.append(msg)


bot_service = ROOT / "apps/api/app/services/bot_service.py"
runtime_factory = ROOT / "services/trading-core/trading_core/runtime/runtime_factory.py"
policy_service = ROOT / "apps/api/app/services/environment_runtime_policy.py"

check(policy_service.exists(), "environment_runtime_policy service exists")

if bot_service.exists():
    src = bot_service.read_text()
    check("enforce_stub_runtime_allowed" in src, "bot_service enforces no-stub runtime policy")
    check("stub_runtime_forbidden_in_staging_or_production" in (policy_service.read_text() if policy_service.exists() else ""), "policy forbids stub runtime in staging/production")

if runtime_factory.exists():
    src = runtime_factory.read_text()
    check("APP_ENV" in src, "runtime_factory reads APP_ENV")
    check("ALLOW_STUB_RUNTIME" in src, "runtime_factory reads ALLOW_STUB_RUNTIME")
    check("stub_runtime_forbidden_in_staging_or_production" in src, "runtime_factory blocks stub in staging/production")

if ERRORS:
    print("\n[FAIL] verify_no_stub_in_prod", file=sys.stderr)
    for err in ERRORS:
        print(f"  - {err}", file=sys.stderr)
    sys.exit(1)

print("[verify_no_stub_in_prod] OK")
