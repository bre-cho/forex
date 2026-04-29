#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import re
import sys

root = Path(__file__).resolve().parents[2]
versions_dir = root / "apps" / "api" / "alembic" / "versions"

rev_re = re.compile(r"^revision\s*=\s*['\"]([^'\"]+)['\"]", re.M)
down_re = re.compile(r"^down_revision\s*=\s*['\"]([^'\"]+)['\"]|^down_revision\s*=\s*None", re.M)

revisions: dict[str, str | None] = {}
all_down_refs: set[str] = set()

for file in sorted(versions_dir.glob("*.py")):
    text = file.read_text()
    rev_m = rev_re.search(text)
    if not rev_m:
        continue
    rev = rev_m.group(1)
    down_m = down_re.search(text)
    down: str | None = None
    if down_m:
        if down_m.group(1):
            down = down_m.group(1)
            all_down_refs.add(down)
    revisions[rev] = down

if not revisions:
    print("[verify_alembic_single_head] no revisions found")
    sys.exit(1)

heads = sorted([rev for rev in revisions if rev not in all_down_refs])
if len(heads) != 1:
    print(f"[verify_alembic_single_head] expected single head, got {len(heads)}: {heads}")
    sys.exit(1)

print(f"[verify_alembic_single_head] OK: single head {heads[0]}")
