"""cTrader market-data adapter.

Separates candle/price health from execution responsibilities.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass
class MarketDataHealth:
    status: str
    reason: str = ""


class CTraderMarketDataAdapter:
    def __init__(self, engine_provider: Any) -> None:
        self._provider = engine_provider

    def get_candles(self, *, limit: int = 200) -> pd.DataFrame:
        if self._provider and hasattr(self._provider, "get_candles"):
            return self._provider.get_candles(limit=limit)
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    def health_check(self) -> MarketDataHealth:
        if self._provider is None:
            return MarketDataHealth(status="degraded", reason="market_data_provider_unavailable")
        return MarketDataHealth(status="healthy", reason="")
