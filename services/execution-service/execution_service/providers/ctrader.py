"""cTrader broker provider — wraps the ctrader_provider engine."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import pandas as pd

from .base import AccountInfo, BrokerProvider, OrderRequest, OrderResult

logger = logging.getLogger(__name__)


class CTraderProvider(BrokerProvider):
    """
    cTrader Open API provider.
    Delegates to the underlying CTraderDataProvider from the engine package,
    while conforming to the BrokerProvider interface.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        access_token: str,
        refresh_token: str,
        account_id: int,
        symbol: str = "EURUSD",
        timeframe: str = "M5",
        live: bool = False,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._account_id = account_id
        self.symbol = symbol
        self.timeframe = timeframe
        self.live = live
        self._provider = None
        self._connected = False

    async def connect(self) -> None:
        try:
            from trading_core.engines.ctrader_provider import CTraderDataProvider

            self._provider = CTraderDataProvider(
                symbol=self.symbol,
                timeframe=self.timeframe,
            )
            self._connected = True
            logger.info("CTraderProvider connected: %s", self.symbol)
        except ImportError:
            if self.live:
                # P3: live mode must fail closed — stub is not acceptable
                raise RuntimeError(
                    "CTraderProvider live mode requires trading_core to be installed. "
                    "Ensure trading_core.engines.ctrader_provider is available."
                )
            logger.warning("trading_core not available; CTraderProvider running in stub/paper mode only")
            self._connected = False

    async def disconnect(self) -> None:
        self._connected = False
        logger.info("CTraderProvider disconnected")

    async def get_account_info(self) -> AccountInfo:
        if self._provider and hasattr(self._provider, "get_account_info"):
            info = await self._provider.get_account_info()
            return AccountInfo(
                balance=info.get("balance", 0),
                equity=info.get("equity", 0),
                margin=info.get("margin", 0),
                free_margin=info.get("free_margin", 0),
                margin_level=info.get("margin_level", 0),
                currency=info.get("currency", "USD"),
            )
        return AccountInfo(
            balance=0, equity=0, margin=0,
            free_margin=0, margin_level=0, currency="USD",
        )

    async def get_candles(self, symbol: str, timeframe: str, limit: int = 200) -> pd.DataFrame:
        if self._provider and hasattr(self._provider, "get_candles"):
            return self._provider.get_candles(limit=limit)
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    async def place_order(self, request: OrderRequest) -> OrderResult:
        if self._provider and hasattr(self._provider, "place_market_order"):
            result = await self._provider.place_market_order(
                symbol=request.symbol,
                side=request.side,
                volume=request.volume,
                stop_loss=request.stop_loss,
                take_profit=request.take_profit,
            )
            return OrderResult(
                order_id=str(result.get("orderId", "")),
                symbol=request.symbol,
                side=request.side,
                volume=request.volume,
                fill_price=result.get("executionPrice", 0),
                commission=result.get("commission", 0),
                success=True,
            )
        return OrderResult(
            order_id="", symbol=request.symbol, side=request.side,
            volume=request.volume, fill_price=0, commission=0,
            success=False, error_message="Provider not connected",
        )

    async def close_position(self, position_id: str) -> OrderResult:
        if self._provider and hasattr(self._provider, "close_position"):
            result = await self._provider.close_position(position_id=int(position_id))
            return OrderResult(
                order_id=position_id, symbol="", side="close",
                volume=result.get("volume", 0),
                fill_price=result.get("executionPrice", 0),
                commission=0, success=True,
            )
        return OrderResult(
            order_id=position_id, symbol="", side="close", volume=0,
            fill_price=0, commission=0, success=False,
            error_message="Provider not connected",
        )

    async def get_open_positions(self) -> List[Dict[str, Any]]:
        if self._provider and hasattr(self._provider, "get_positions"):
            return await self._provider.get_positions()
        return []

    async def get_trade_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        if self._provider and hasattr(self._provider, "get_history"):
            return await self._provider.get_history(limit=limit)
        return []

    async def health_check(self) -> Dict[str, Any]:
        if not self._connected:
            return {"status": "disconnected", "reason": "provider_not_connected"}
        if self._provider is None:
            return {"status": "degraded", "reason": "provider_running_in_stub_mode"}
        return {"status": "healthy", "reason": ""}

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def mode(self) -> str:
        if not self._connected:
            return "unavailable"
        if self._provider is None:
            return "stub"
        return "live" if self.live else "demo"
