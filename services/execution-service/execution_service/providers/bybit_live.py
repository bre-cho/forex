from __future__ import annotations

from .bybit import BybitProvider


class BybitLiveProvider(BybitProvider):
    """Explicit live-only Bybit provider."""

    def __init__(self, *args, **kwargs):
        kwargs["testnet"] = True
        super().__init__(*args, **kwargs)
        self.testnet = False
        self.mode = "live"
