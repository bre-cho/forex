#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import re
import sys

root = Path(__file__).resolve().parents[2]
web_lib = root / "apps" / "web" / "lib"
main_file = root / "apps" / "api" / "app" / "main.py"
live_router = root / "apps" / "api" / "app" / "routers" / "live_trading.py"

required_libs = [
    web_lib / "runtimeApi.ts",
    web_lib / "brokerApi.ts",
    web_lib / "decisionLedgerApi.ts",
    web_lib / "riskPolicyApi.ts",
    web_lib / "incidentApi.ts",
]

missing = [str(p) for p in required_libs if not p.exists()]
if missing:
    print("[verify_frontend_api_contract] missing web API clients:")
    for m in missing:
        print(" -", m)
    sys.exit(1)

src_main = main_file.read_text()
src_router = live_router.read_text() if live_router.exists() else ""

if "live_trading" not in src_main:
    print("[verify_frontend_api_contract] live_trading router not registered in apps/api/app/main.py")
    sys.exit(1)

# Ensure key API route fragments exist for frontend libs.
expected_fragments = [
    "/decision-ledger",
    "/gate-events",
    "/daily-state",
    "/incidents",
]
for frag in expected_fragments:
    if frag not in src_router:
        print(f"[verify_frontend_api_contract] missing route fragment: {frag}")
        sys.exit(1)

print("[verify_frontend_api_contract] OK")
