from __future__ import annotations

from .mt5 import MT5Provider


class MT5LiveProvider(MT5Provider):
    """Explicit live-only MT5 provider."""

    def __init__(self, *args, **kwargs):
        kwargs["mode"] = "live"
        kwargs["_allow_live"] = True
        super().__init__(*args, **kwargs)
