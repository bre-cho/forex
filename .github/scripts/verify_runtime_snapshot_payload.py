#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import pathlib
import sys

root = pathlib.Path(__file__).resolve().parents[2]
runtime_state_path = root / "services" / "trading-core" / "trading_core" / "runtime" / "runtime_state.py"

spec = importlib.util.spec_from_file_location("runtime_state", runtime_state_path)
if spec is None or spec.loader is None:
    print("[verify_runtime_snapshot_payload] cannot load runtime_state module")
    sys.exit(1)

module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

RuntimeState = getattr(module, "RuntimeState", None)
if RuntimeState is None:
    print("[verify_runtime_snapshot_payload] RuntimeState missing")
    sys.exit(1)

snapshot = RuntimeState(bot_instance_id="verify-bot").to_dict()
required = [
    "bot_instance_id",
    "status",
    "started_at",
    "stopped_at",
    "balance",
    "equity",
    "daily_pnl",
    "open_trades",
    "total_trades",
    "error_message",
    "metadata",
    "uptime_seconds",
]
missing = [k for k in required if k not in snapshot]
if missing:
    print(f"[verify_runtime_snapshot_payload] missing keys in RuntimeState.to_dict(): {missing}")
    sys.exit(1)

bots_router = root / "apps" / "api" / "app" / "routers" / "bots.py"
src = bots_router.read_text()
if "_runtime_snapshot_not_running" not in src:
    print("[verify_runtime_snapshot_payload] missing _runtime_snapshot_not_running helper in bots router")
    sys.exit(1)
if "return _runtime_snapshot_not_running(bot_id)" not in src:
    print("[verify_runtime_snapshot_payload] /runtime endpoint no longer returns full not_running payload")
    sys.exit(1)

print("[verify_runtime_snapshot_payload] OK")
