#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

root = Path(__file__).resolve().parents[2]
engine_file = root / "services" / "execution-service" / "execution_service" / "execution_engine.py"
bot_runtime_file = root / "services" / "trading-core" / "trading_core" / "runtime" / "bot_runtime.py"

engine_src = engine_file.read_text()
runtime_src = bot_runtime_file.read_text()

violations: list[str] = []

if "PreExecutionGate" not in engine_src:
    violations.append("ExecutionEngine must import/use PreExecutionGate")
if "execution_gate_blocked" not in engine_src:
    violations.append("ExecutionEngine.place_order must block on gate failures")
if "runtime_mode=self.runtime_mode" not in runtime_src:
    violations.append("BotRuntime must pass runtime_mode into ExecutionEngine")
if "gate_policy=gate_policy" not in runtime_src:
    violations.append("BotRuntime must pass gate_policy into ExecutionEngine")
if "if self.runtime_mode == \"live\":" not in runtime_src or "await self._execute_signal(trade_signal)" not in runtime_src:
    violations.append("BotRuntime live mode must execute through direct controlled path")

if violations:
    print("[verify_broker_gate_wiring] FAIL")
    for v in violations:
        print(" -", v)
    sys.exit(1)

print("[verify_broker_gate_wiring] OK")
