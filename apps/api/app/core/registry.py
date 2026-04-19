"""Global RuntimeRegistry singleton accessor."""
from __future__ import annotations

from typing import Optional

_registry = None


def get_registry():
    return _registry


def set_registry(registry) -> None:
    global _registry
    _registry = registry
