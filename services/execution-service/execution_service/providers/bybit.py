"""Bybit provider — optional real adapter, fails closed when SDK absent.

Requires ``pybit`` package (pip install pybit).
In live mode without the package the provider refuses to connect.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import pandas as pd

from .base import AccountInfo, BrokerProvider, OrderRequest, OrderResult

logger = logging.getLogger(__name__)

try:
    from pybit.unified_trading import HTTP as _BybitHTTP  # type: ignore[import]
    _BYBIT_AVAILABLE = True
except ImportError:
    _BybitHTTP = None  # type: ignore[assignment]
    _BYBIT_AVAILABLE = False

_TF_MAP = {
    "M1": "1", "M5": "5", "M15": "15", "M30": "30",
    "H1": "60", "H4": "240", "D1": "D",
}


class BybitProvider(BrokerProvider):
    """Bybit V5 provider — real adapter when pybit available."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        symbol: str = "BTCUSDT",
        timeframe: str = "M5",
        testnet: bool = True,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.symbol = symbol
        self.timeframe = timeframe
        self.testnet = testnet
        self.mode = "demo" if testnet else "live"
        self._session: Optional[Any] = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _require_sdk(self) -> None:
        if not _BYBIT_AVAILABLE:
            raise RuntimeError(
                "BybitProvider requires the pybit package. Install: pip install pybit"
            )

    async def connect(self) -> None:
        if not self.testnet:
            self._require_sdk()
        if not _BYBIT_AVAILABLE:
            logger.warning("pybit SDK unavailable; BybitProvider in stub/paper mode only")
            self._connected = False
            return
        self._session = _BybitHTTP(
            testnet=self.testnet,
            api_key=self.api_key,
            api_secret=self.api_secret,
        )
        resp = self._session.get_wallet_balance(accountType="UNIFIED")
        if resp.get("retCode") != 0:
            raise ConnectionError(f"Bybit connect failed: {resp.get('retMsg')}")
        self._connected = True
        logger.info("BybitProvider connected: symbol=%s testnet=%s", self.symbol, self.testnet)

    async def disconnect(self) -> None:
        self._session = None
        self._connected = False

    async def get_account_info(self) -> AccountInfo:
        self._require_sdk()
        resp = self._session.get_wallet_balance(accountType="UNIFIED")
        if resp.get("retCode") != 0:
            raise RuntimeError(f"Bybit wallet: {resp.get('retMsg')}")
        coins = resp["result"]["list"][0]["coin"]
        usdt = next((c for c in coins if c["coin"] == "USDT"), {})
        equity = float(usdt.get("equity", 0))
        avail = float(usdt.get("availableToWithdraw", 0))
        return AccountInfo(
            balance=equity,
            equity=equity,
            margin=equity - avail,
            free_margin=avail,
            margin_level=0.0,
            currency="USDT",
        )

    async def get_candles(self, symbol: str, timeframe: str, limit: int = 200) -> pd.DataFrame:
        self._require_sdk()
        interval = _TF_MAP.get(timeframe, "5")
        resp = self._session.get_kline(category="linear", symbol=symbol, interval=interval, limit=limit)
        if resp.get("retCode") != 0:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        rows = resp["result"]["list"]
        df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume", "turnover"])
        df["time"] = pd.to_datetime(df["time"].astype(int), unit="ms")
        df.set_index("time", inplace=True)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
        return df[["open", "high", "low", "close", "volume"]].sort_index()

    async def place_order(self, request: OrderRequest) -> OrderResult:
        self._require_sdk()
        resp = self._session.place_order(
            category="linear",
            symbol=request.symbol,
            side="Buy" if request.side.lower() == "buy" else "Sell",
            orderType="Market",
            qty=str(request.volume),
            stopLoss=str(request.stop_loss) if request.stop_loss else None,
            takeProfit=str(request.take_profit) if request.take_profit else None,
            orderLinkId=str(request.comment or "")[:36],
        )
        if resp.get("retCode") != 0:
            return OrderResult(success=False, error_message=f"Bybit order failed: {resp.get('retMsg')}")
        order_id = resp["result"]["orderId"]
        return OrderResult(
            success=True,
            order_id=str(order_id),
            symbol=request.symbol,
            side=request.side,
            volume=request.volume,
            fill_price=request.price or 0.0,
        )

    async def close_position(self, position_id: str) -> OrderResult:
        self._require_sdk()
        positions = await self.get_open_positions()
        pos = next((p for p in positions if p.get("id") == position_id), None)
        if pos is None:
            return OrderResult(success=False, error_message=f"Position {position_id} not found")
        close_side = "Sell" if pos["side"] == "BUY" else "Buy"
        resp = self._session.place_order(
            category="linear",
            symbol=pos["symbol"],
            side=close_side,
            orderType="Market",
            qty=str(pos["volume"]),
            reduceOnly=True,
        )
        if resp.get("retCode") != 0:
            return OrderResult(success=False, error_message=f"Bybit close failed: {resp.get('retMsg')}")
        return OrderResult(success=True, order_id=resp["result"]["orderId"], symbol=pos["symbol"])

    async def get_open_positions(self) -> List[Dict[str, Any]]:
        self._require_sdk()
        resp = self._session.get_positions(category="linear", symbol=self.symbol)
        if resp.get("retCode") != 0:
            return []
        return [
            {
                "id": p["positionIdx"],
                "symbol": p["symbol"],
                "side": p["side"].upper(),
                "volume": float(p["size"]),
                "open_price": float(p["avgPrice"]),
                "sl": float(p.get("stopLoss") or 0),
                "tp": float(p.get("takeProfit") or 0),
                "profit": float(p.get("unrealisedPnl") or 0),
            }
            for p in resp["result"]["list"]
            if float(p.get("size", 0)) > 0
        ]

    async def get_trade_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        self._require_sdk()
        resp = self._session.get_executions(category="linear", limit=limit)
        if resp.get("retCode") != 0:
            return []
        return [
            {
                "id": t["execId"],
                "symbol": t["symbol"],
                "side": t["side"].upper(),
                "volume": float(t["execQty"]),
                "price": float(t["execPrice"]),
                "profit": float(t.get("closedPnl", 0)),
                "time": t["execTime"],
            }
            for t in resp["result"]["list"]
        ]

