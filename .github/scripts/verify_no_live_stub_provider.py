#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

root = Path(__file__).resolve().parents[2]
runtime_file = root / "services" / "trading-core" / "trading_core" / "runtime" / "bot_runtime.py"

src = runtime_file.read_text(encoding="utf-8", errors="ignore")
violations: list[str] = []

if "provider_mode_not_allowed" not in src:
    violations.append("BotRuntime must hard-fail live provider modes {stub,paper,degraded,unavailable}")

required_modes = ["stub", "paper", "degraded", "unavailable"]
if "provider_mode in {" not in src:
    violations.append("Live provider blocked-mode check expression missing")
for mode in required_modes:
    if f'"{mode}"' not in src and f"'{mode}'" not in src:
        violations.append(f"Live provider blocked-mode set missing '{mode}'")

if violations:
    print("[verify_no_live_stub_provider] FAIL")
    for v in violations:
        print(" -", v)
    sys.exit(1)

print("[verify_no_live_stub_provider] OK")
