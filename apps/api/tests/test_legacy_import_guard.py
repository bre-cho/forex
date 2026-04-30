"""CI guard: production code must not import deprecated legacy packages.

Blocks accidental import coupling from live services into `backend.*` / `frontend.*`.
"""
from __future__ import annotations

from pathlib import Path


def test_no_legacy_imports_in_apps_services() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    scan_roots = [repo_root / "apps", repo_root / "services"]
    forbidden_import_prefixes = (
        "from backend",
        "from frontend",
        "import backend",
        "import frontend",
    )

    violations: list[str] = []
    for root in scan_roots:
        for py_file in root.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8", errors="ignore")
            for line_no, line in enumerate(text.splitlines(), start=1):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if stripped.startswith(("from ", "import ")) and any(
                    stripped.startswith(token) for token in forbidden_import_prefixes
                ):
                    rel = py_file.relative_to(repo_root)
                    violations.append(f"{rel}:{line_no}:{stripped}")

    assert not violations, "\n".join(["legacy imports found:"] + violations)
