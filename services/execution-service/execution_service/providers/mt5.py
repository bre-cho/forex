"""MetaTrader 5 provider — roadmap item, not yet production-ready.

Support matrix
--------------
| Provider  | Status      | Notes                               |
|-----------|-------------|-------------------------------------|
| paper     | ✅ Stable   | Full in-process simulation          |
| ctrader   | ✅ Stable   | cTrader Open API                    |
| mt5       | 🚧 Roadmap  | Requires MetaTrader5 Python package |
| bybit     | 🚧 Roadmap  | Bybit V5 REST/WebSocket             |

MT5 requires the proprietary ``MetaTrader5`` Python package (Windows-only DLL).
Do NOT instantiate this provider in production until the implementation is
complete and the package is included in the dependency lockfile.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

from .base import AccountInfo, BrokerProvider, OrderRequest, OrderResult

_NOT_IMPLEMENTED_MSG = (
    "MT5Provider is on the roadmap but not yet implemented. "
    "Use PaperProvider or CTraderProvider instead."
)


class MT5Provider(BrokerProvider):
    """MetaTrader 5 provider stub — roadmap item, not production-ready."""

    @property
    def is_connected(self) -> bool:
        return False

    async def connect(self) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    async def disconnect(self) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    async def get_account_info(self) -> AccountInfo:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    async def get_candles(self, symbol: str, timeframe: str, limit: int = 200) -> pd.DataFrame:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    async def place_order(self, request: OrderRequest) -> OrderResult:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    async def close_position(self, position_id: str) -> OrderResult:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    async def get_open_positions(self) -> List[Dict[str, Any]]:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    async def get_trade_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

