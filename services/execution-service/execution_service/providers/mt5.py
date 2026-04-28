"""MetaTrader 5 provider — optional real adapter, fails closed when SDK absent.

Requires the proprietary ``MetaTrader5`` Python package (Windows-only DLL).
In live mode without the package the provider refuses to connect.
In paper/dev mode it falls through to a safe stub. 
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import pandas as pd

from .base import AccountInfo, BrokerProvider, OrderRequest, OrderResult

logger = logging.getLogger(__name__)

try:
    import MetaTrader5 as _mt5_sdk  # type: ignore[import]
    _MT5_AVAILABLE = True
except ImportError:
    _mt5_sdk = None  # type: ignore[assignment]
    _MT5_AVAILABLE = False

_TF_MAP = {
    "M1": 1, "M5": 5, "M15": 15, "M30": 30,
    "H1": 16385, "H4": 16388, "D1": 16408,
}


class MT5Provider(BrokerProvider):
    """MetaTrader 5 provider — real adapter when SDK available."""

    def __init__(
        self,
        login: int,
        password: str,
        server: str,
        symbol: str = "EURUSD",
        timeframe: str = "M5",
        live: bool = False,
    ) -> None:
        self.login = login
        self.password = password
        self.server = server
        self.symbol = symbol
        self.timeframe = timeframe
        self.live = live
        self.mode = "live" if live else "demo"
        self._connected = False
        self._account_id: Optional[int] = None

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _require_sdk(self) -> None:
        if not _MT5_AVAILABLE:
            raise RuntimeError(
                "MT5Provider requires the MetaTrader5 package. "
                "Install it on a Windows host or use PaperProvider."
            )

    async def connect(self) -> None:
        if self.live:
            self._require_sdk()
        if not _MT5_AVAILABLE:
            logger.warning("MT5 SDK unavailable; running in stub/paper mode only")
            self._connected = False
            return
        if not _mt5_sdk.initialize(login=self.login, password=self.password, server=self.server):
            raise ConnectionError(f"MT5 initialize failed: {_mt5_sdk.last_error()}")
        info = _mt5_sdk.account_info()
        if info is None:
            raise ConnectionError(f"MT5 account_info failed: {_mt5_sdk.last_error()}")
        self._account_id = info.login
        self._connected = True
        logger.info("MT5Provider connected: login=%s server=%s", info.login, self.server)

    async def disconnect(self) -> None:
        if _MT5_AVAILABLE:
            _mt5_sdk.shutdown()
        self._connected = False

    async def get_account_info(self) -> AccountInfo:
        self._require_sdk()
        info = _mt5_sdk.account_info()
        if info is None:
            raise RuntimeError(f"MT5 account_info: {_mt5_sdk.last_error()}")
        return AccountInfo(
            balance=float(info.balance),
            equity=float(info.equity),
            margin=float(info.margin),
            free_margin=float(info.margin_free),
            margin_level=float(info.margin_level),
            currency=str(info.currency),
        )

    async def get_candles(self, symbol: str, timeframe: str, limit: int = 200) -> pd.DataFrame:
        self._require_sdk()
        tf = _TF_MAP.get(timeframe, 5)
        rates = _mt5_sdk.copy_rates_from_pos(symbol, tf, 0, limit)
        if rates is None or len(rates) == 0:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df.set_index("time", inplace=True)
        df.rename(columns={"tick_volume": "volume"}, inplace=True)
        return df[["open", "high", "low", "close", "volume"]]

    async def place_order(self, request: OrderRequest) -> OrderResult:
        self._require_sdk()
        mt5_order_type = _mt5_sdk.ORDER_TYPE_BUY if request.side.lower() == "buy" else _mt5_sdk.ORDER_TYPE_SELL
        symbol_info = _mt5_sdk.symbol_info(request.symbol)
        if symbol_info is None:
            return OrderResult(success=False, error_message=f"Symbol not found: {request.symbol}")
        volume_step = symbol_info.volume_step or 0.01
        volume = round(round(request.volume / volume_step) * volume_step, 8)
        volume = max(symbol_info.volume_min, min(symbol_info.volume_max, volume))
        req = {
            "action": _mt5_sdk.TRADE_ACTION_DEAL,
            "symbol": request.symbol,
            "volume": volume,
            "type": mt5_order_type,
            "price": _mt5_sdk.symbol_info_tick(request.symbol).ask if request.side.lower() == "buy" else _mt5_sdk.symbol_info_tick(request.symbol).bid,
            "sl": request.stop_loss or 0.0,
            "tp": request.take_profit or 0.0,
            "comment": str(request.comment or "")[:31],
            "type_filling": _mt5_sdk.ORDER_FILLING_IOC,
        }
        result = _mt5_sdk.order_send(req)
        if result is None or result.retcode != _mt5_sdk.TRADE_RETCODE_DONE:
            code = getattr(result, "retcode", -1)
            return OrderResult(success=False, error_message=f"MT5 order failed retcode={code}")
        return OrderResult(
            success=True,
            order_id=str(result.order),
            symbol=request.symbol,
            side=request.side,
            volume=float(result.volume),
            fill_price=float(result.price),
        )

    async def close_position(self, position_id: str) -> OrderResult:
        self._require_sdk()
        positions = _mt5_sdk.positions_get(ticket=int(position_id))
        if not positions:
            return OrderResult(success=False, error_message=f"Position {position_id} not found")
        pos = positions[0]
        close_type = _mt5_sdk.ORDER_TYPE_SELL if pos.type == 0 else _mt5_sdk.ORDER_TYPE_BUY
        tick = _mt5_sdk.symbol_info_tick(pos.symbol)
        price = tick.bid if close_type == _mt5_sdk.ORDER_TYPE_SELL else tick.ask
        req = {
            "action": _mt5_sdk.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": close_type,
            "position": pos.ticket,
            "price": price,
            "comment": "close",
            "type_filling": _mt5_sdk.ORDER_FILLING_IOC,
        }
        result = _mt5_sdk.order_send(req)
        if result is None or result.retcode != _mt5_sdk.TRADE_RETCODE_DONE:
            return OrderResult(success=False, error_message=f"MT5 close failed retcode={getattr(result, 'retcode', -1)}")
        return OrderResult(success=True, order_id=str(result.order), symbol=pos.symbol)

    async def get_open_positions(self) -> List[Dict[str, Any]]:
        self._require_sdk()
        positions = _mt5_sdk.positions_get() or []
        return [
            {
                "id": str(p.ticket),
                "symbol": p.symbol,
                "side": "BUY" if p.type == 0 else "SELL",
                "volume": p.volume,
                "open_price": p.price_open,
                "sl": p.sl,
                "tp": p.tp,
                "profit": p.profit,
            }
            for p in positions
        ]

    async def get_trade_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        self._require_sdk()
        from datetime import datetime, timedelta
        from_date = datetime.now() - timedelta(days=30)
        deals = _mt5_sdk.history_deals_get(from_date, datetime.now()) or []
        return [
            {
                "id": str(d.ticket),
                "order": str(d.order),
                "symbol": d.symbol,
                "side": "BUY" if d.type == 0 else "SELL",
                "volume": d.volume,
                "price": d.price,
                "profit": d.profit,
                "time": str(datetime.fromtimestamp(d.time)),
            }
            for d in list(deals)[-limit:]
        ]

