#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import re
import sys

root = Path(__file__).resolve().parents[2]
engine_file = root / "services" / "execution-service" / "execution_service" / "execution_engine.py"
bot_runtime_file = root / "services" / "trading-core" / "trading_core" / "runtime" / "bot_runtime.py"
order_router_file = root / "services" / "execution-service" / "execution_service" / "order_router.py"

engine_src = engine_file.read_text()
runtime_src = bot_runtime_file.read_text()
order_router_src = order_router_file.read_text()

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
if "execution_gate_blocked:execution_command_required" not in engine_src:
    violations.append("ExecutionEngine must reject raw OrderRequest in live mode")
if "route(self._provider_name, request)" not in engine_src or "await provider.place_order(request)" not in order_router_src:
    violations.append("OrderRouter must remain the single provider.place_order dispatch path")

python_files = [p for p in root.rglob("*.py") if "/.venv/" not in str(p) and "__pycache__" not in str(p)]
for path in python_files:
    rel = path.relative_to(root).as_posix()
    if rel.startswith("services/execution-service/tests/"):
        continue
    if rel == "services/execution-service/execution_service/order_router.py":
        continue
    if rel == ".github/scripts/verify_broker_gate_wiring.py":
        continue
    try:
        text = path.read_text()
    except UnicodeDecodeError:
        continue
    if re.search(r"\bprovider\s*\.\s*place_order\s*\(", text):
        violations.append(f"Direct provider.place_order call found outside order_router/tests: {rel}")

if violations:
    print("[verify_broker_gate_wiring] FAIL")
    for v in violations:
        print(" -", v)
    sys.exit(1)

print("[verify_broker_gate_wiring] OK")
