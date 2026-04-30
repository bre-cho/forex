from __future__ import annotations

from .bybit import BybitProvider


class BybitLiveProvider(BybitProvider):
    """Explicit live-only Bybit provider."""

    def __init__(self, *args, **kwargs):
        kwargs["mode"] = "live"
        kwargs["_allow_live"] = True
        super().__init__(*args, **kwargs)
