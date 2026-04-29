from __future__ import annotations

from .ctrader import CTraderProvider


class CTraderDemoProvider(CTraderProvider):
    """Explicit demo-only cTrader provider."""

    def __init__(self, *args, **kwargs):
        kwargs["live"] = False
        super().__init__(*args, **kwargs)
