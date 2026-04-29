#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import ast
import sys

root = Path(__file__).resolve().parents[2]
service_file = root / "apps" / "api" / "app" / "services" / "experiment_registry_service.py"

expected = [
    "DRAFT",
    "PAPER_TEST",
    "DEMO_TEST",
    "MICRO_LIVE",
    "LIVE_APPROVED",
    "RETIRED",
]

src = service_file.read_text()
tree = ast.parse(src)
stages = None
has_regression_guard = "stage_regression_not_allowed" in src

for node in ast.walk(tree):
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "_STAGES":
                if isinstance(node.value, (ast.List, ast.Tuple)):
                    vals = []
                    for elt in node.value.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            vals.append(elt.value)
                    stages = vals

if stages is None:
    print("[verify_experiment_stage_policy] _STAGES not found")
    sys.exit(1)

if stages != expected:
    print("[verify_experiment_stage_policy] invalid stage order")
    print(" expected:", expected)
    print(" actual  :", stages)
    sys.exit(1)

if len(set(stages)) != len(stages):
    print("[verify_experiment_stage_policy] duplicate stage detected")
    sys.exit(1)

if not has_regression_guard:
    print("[verify_experiment_stage_policy] missing regression guard")
    sys.exit(1)

print("[verify_experiment_stage_policy] OK")
