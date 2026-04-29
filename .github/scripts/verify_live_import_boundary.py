#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import ast
import sys

root = Path(__file__).resolve().parents[2]

scan_roots = [
    root / "apps" / "api",
    root / "services" / "trading-core",
    root / "services" / "execution-service",
]

violations: list[str] = []

for scan_root in scan_roots:
    for path in scan_root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "backend" or alias.name.startswith("backend."):
                        violations.append(f"Live boundary violation (backend import): {rel}")
                        break
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "backend" or module.startswith("backend."):
                    violations.append(f"Live boundary violation (backend import): {rel}")
                    break

if violations:
    print("[verify_live_import_boundary] FAIL")
    for v in violations:
        print(" -", v)
    sys.exit(1)

print("[verify_live_import_boundary] OK")
