from __future__ import annotations

from .ctrader import CTraderProvider


class CTraderLiveProvider(CTraderProvider):
    """Live-only cTrader provider wrapper.

    This class hard-pins live mode to keep live wiring explicit at composition time.
    """

    def __init__(self, *args, **kwargs):
        # Base CTraderProvider is demo-only by contract.
        kwargs["live"] = False
        super().__init__(*args, **kwargs)
        self.live = True
