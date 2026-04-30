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


svc = ROOT / "apps/api/app/services/provider_certification_service.py"
router = ROOT / "apps/api/app/routers/provider_certification.py"
migration = ROOT / "apps/api/alembic/versions/0024_provider_certification.py"
model = ROOT / "apps/api/app/models/__init__.py"
preflight = ROOT / "apps/api/app/services/live_start_preflight.py"
main_py = ROOT / "apps/api/app/main.py"

check(svc.exists(), "ProviderCertificationService exists")
check(router.exists(), "provider_certification router exists")
check(migration.exists(), "alembic migration 0024_provider_certification exists")

if model.exists():
    src = model.read_text()
    check("class ProviderCertification" in src, "ProviderCertification model exists")
    check('"provider_certifications"' in src or "'provider_certifications'" in src, "provider_certifications table mapped")
    check("expires_at" in src, "ProviderCertification has expires_at")
    check("revoked_at" in src, "ProviderCertification has revoked_at")

if preflight.exists():
    src = preflight.read_text()
    check("ProviderCertificationService" in src, "live_start_preflight imports ProviderCertificationService")
    check("provider_not_live_certified" in src, "live_start_preflight blocks when provider is not certified")

if svc.exists():
    svc_src = svc.read_text()
    check("provider_certification_expired" in svc_src, "provider certification service handles expired certification")
    check("provider_certification_revoked" in svc_src, "provider certification service handles revoked certification")

if main_py.exists():
    src = main_py.read_text()
    check("provider_certification" in src, "main.py wires provider_certification router")

if ERRORS:
    print("\n[FAIL] verify_provider_certification_gate", file=sys.stderr)
    for err in ERRORS:
        print(f"  - {err}", file=sys.stderr)
    sys.exit(1)

print("[verify_provider_certification_gate] OK")
