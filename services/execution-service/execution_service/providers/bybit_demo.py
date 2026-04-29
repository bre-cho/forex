from __future__ import annotations

from .bybit import BybitProvider


class BybitDemoProvider(BybitProvider):
    """Explicit demo-only Bybit provider."""

    def __init__(self, *args, **kwargs):
        kwargs["testnet"] = True
        super().__init__(*args, **kwargs)
