"""MetaTrader 5 provider stub."""
from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

from .base import AccountInfo, BrokerProvider, OrderRequest, OrderResult


class MT5Provider(BrokerProvider):
    """MetaTrader 5 provider — not yet implemented."""

    async def connect(self) -> None:
        raise NotImplementedError("MT5Provider is not yet implemented")

    async def disconnect(self) -> None:
        raise NotImplementedError("MT5Provider is not yet implemented")

    async def get_account_info(self) -> AccountInfo:
        raise NotImplementedError("MT5Provider is not yet implemented")

    async def get_candles(self, symbol: str, timeframe: str, limit: int = 200) -> pd.DataFrame:
        raise NotImplementedError("MT5Provider is not yet implemented")

    async def place_order(self, request: OrderRequest) -> OrderResult:
        raise NotImplementedError("MT5Provider is not yet implemented")

    async def close_position(self, position_id: str) -> OrderResult:
        raise NotImplementedError("MT5Provider is not yet implemented")

    async def get_open_positions(self) -> List[Dict[str, Any]]:
        raise NotImplementedError("MT5Provider is not yet implemented")

    async def get_trade_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        raise NotImplementedError("MT5Provider is not yet implemented")

    @property
    def is_connected(self) -> bool:
        return False
