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
        self.provider_name = "mt5"
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

    def _ensure_symbol(self, symbol: str) -> None:
        if not _mt5_sdk.symbol_select(symbol, True):
            raise RuntimeError(f"MT5 symbol_select failed: {symbol}")

    def _normalize_volume(self, symbol_info: Any, volume: float) -> float:
        step = float(getattr(symbol_info, "volume_step", 0.01) or 0.01)
        vmin = float(getattr(symbol_info, "volume_min", step) or step)
        vmax = float(getattr(symbol_info, "volume_max", max(vmin, step)) or max(vmin, step))
        normalized = round(round(volume / step) * step, 8)
        return max(vmin, min(vmax, normalized))

    def _trade_allowed(self, account_info: Any, terminal_info: Any, symbol_info: Any) -> tuple[bool, str]:
        if account_info is None:
            return False, "account_info_unavailable"
        if terminal_info is None:
            return False, "terminal_info_unavailable"
        if not bool(getattr(terminal_info, "connected", True)):
            return False, "terminal_not_connected"
        if not bool(getattr(terminal_info, "trade_allowed", True)):
            return False, "terminal_trade_not_allowed"
        if not bool(getattr(account_info, "trade_allowed", True)):
            return False, "account_trade_not_allowed"
        if symbol_info is None:
            return False, "symbol_info_unavailable"
        if not bool(getattr(symbol_info, "visible", True)):
            return False, "symbol_not_visible"
        trade_mode = int(getattr(symbol_info, "trade_mode", 0) or 0)
        disabled_mode = int(getattr(_mt5_sdk, "SYMBOL_TRADE_MODE_DISABLED", -1) or -1)
        if trade_mode == disabled_mode:
            return False, "symbol_trade_disabled"
        return True, "ok"

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
        self._ensure_symbol(symbol)
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
        self._ensure_symbol(request.symbol)
        symbol_info = _mt5_sdk.symbol_info(request.symbol)
        tick = _mt5_sdk.symbol_info_tick(request.symbol)
        account_info = _mt5_sdk.account_info()
        terminal_info = _mt5_sdk.terminal_info()
        if symbol_info is None or tick is None:
            return OrderResult(order_id="", symbol=request.symbol, side=request.side, volume=float(request.volume), fill_price=float(request.price or 0.0), commission=0.0, success=False, error_message=f"Symbol/tick unavailable: {request.symbol}")
        trade_allowed, reason = self._trade_allowed(account_info, terminal_info, symbol_info)
        if not trade_allowed:
            return OrderResult(order_id="", symbol=request.symbol, side=request.side, volume=float(request.volume), fill_price=float(request.price or 0.0), commission=0.0, success=False, error_message=f"MT5 preflight failed: {reason}")
        volume = self._normalize_volume(symbol_info, request.volume)
        spread = abs(float(tick.ask) - float(tick.bid))
        max_spread = float(getattr(symbol_info, "point", 0.0001) or 0.0001) * 30
        if spread > max_spread:
            return OrderResult(order_id="", symbol=request.symbol, side=request.side, volume=float(volume), fill_price=float(request.price or 0.0), commission=0.0, success=False, error_message=f"Spread too high: {spread}")
        order_price = float(tick.ask) if request.side.lower() == "buy" else float(tick.bid)
        if hasattr(_mt5_sdk, "order_calc_margin"):
            try:
                margin_required = _mt5_sdk.order_calc_margin(mt5_order_type, request.symbol, volume, order_price)
            except Exception:
                margin_required = None
            free_margin = float(getattr(account_info, "margin_free", 0.0) or 0.0)
            if margin_required is not None and free_margin > 0 and float(margin_required) > free_margin:
                return OrderResult(order_id="", symbol=request.symbol, side=request.side, volume=float(volume), fill_price=order_price, commission=0.0, success=False, error_message="MT5 preflight failed: insufficient_free_margin")
        req = {
            "action": _mt5_sdk.TRADE_ACTION_DEAL,
            "symbol": request.symbol,
            "volume": volume,
            "type": mt5_order_type,
            "price": order_price,
            "sl": request.stop_loss or 0.0,
            "tp": request.take_profit or 0.0,
            "deviation": 20,
            "magic": 20260428,
            "comment": str(request.comment or "")[:31],
            "type_filling": _mt5_sdk.ORDER_FILLING_IOC,
        }
        result = _mt5_sdk.order_send(req)
        if result is None or result.retcode != _mt5_sdk.TRADE_RETCODE_DONE:
            code = getattr(result, "retcode", -1)
            return OrderResult(order_id="", symbol=request.symbol, side=request.side, volume=float(volume), fill_price=order_price, commission=0.0, success=False, error_message=f"MT5 order failed retcode={code}")
        # Verify fill/deal exists for stronger live safety
        deals = _mt5_sdk.history_deals_get(position=result.order) or []
        if deals is None:
            deals = []
        return OrderResult(
            success=True,
            order_id=str(result.order),
            symbol=request.symbol,
            side=request.side,
            volume=float(result.volume),
            fill_price=float(result.price),
            commission=float(sum(float(getattr(d, "commission", 0.0) or 0.0) for d in deals)) if deals else 0.0,
        )

    async def close_position(self, position_id: str) -> OrderResult:
        self._require_sdk()
        positions = _mt5_sdk.positions_get(ticket=int(position_id))
        if not positions:
            return OrderResult(order_id=position_id, symbol="", side="close", volume=0.0, fill_price=0.0, commission=0.0, success=False, error_message=f"Position {position_id} not found")
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
            return OrderResult(order_id=position_id, symbol=pos.symbol, side="close", volume=float(getattr(pos, 'volume', 0.0) or 0.0), fill_price=float(price or 0.0), commission=0.0, success=False, error_message=f"MT5 close failed retcode={getattr(result, 'retcode', -1)}")
        return OrderResult(order_id=str(result.order), symbol=pos.symbol, side="close", volume=float(getattr(pos, 'volume', 0.0) or 0.0), fill_price=float(price or 0.0), commission=0.0, success=True)

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

    @property
    def supports_client_order_id(self) -> bool:
        return True

    async def get_instrument_spec(self, symbol: str) -> Optional[Dict[str, Any]]:
        self._require_sdk()
        self._ensure_symbol(symbol)
        info = _mt5_sdk.symbol_info(symbol)
        if info is None:
            if self.live:
                raise RuntimeError(f"MT5 get_instrument_spec failed for {symbol}: {_mt5_sdk.last_error()}")
            return None
        return {
            "symbol": symbol,
            "min_lot": float(getattr(info, "volume_min", 0.01) or 0.01),
            "max_lot": float(getattr(info, "volume_max", 100.0) or 100.0),
            "lot_step": float(getattr(info, "volume_step", 0.01) or 0.01),
            "contract_size": float(getattr(info, "trade_contract_size", 100000.0) or 100000.0),
            "pip_size": float(getattr(info, "point", 0.0001) or 0.0001),
            "pip_value_per_lot": float(getattr(info, "trade_tick_value", 10.0) or 10.0),
            "margin_rate": float(getattr(info, "margin_initial", 0.01) or 0.01),
            "currency_base": str(getattr(info, "currency_base", "")),
            "currency_profit": str(getattr(info, "currency_profit", "")),
        }

    async def estimate_margin(self, symbol: str, side: str, volume: float, price: float) -> float:
        self._require_sdk()
        mt5_type = _mt5_sdk.ORDER_TYPE_BUY if side.lower() == "buy" else _mt5_sdk.ORDER_TYPE_SELL
        if hasattr(_mt5_sdk, "order_calc_margin"):
            try:
                result = _mt5_sdk.order_calc_margin(mt5_type, symbol, volume, price)
                if result is not None:
                    return float(result)
            except Exception:
                pass
        # Fallback: use contract_size * volume * price * margin_initial
        spec = await self.get_instrument_spec(symbol)
        if spec:
            return volume * float(spec.get("contract_size", 100000.0)) * price * float(spec.get("margin_rate", 0.01))
        return volume * price * 0.01

    async def get_order_by_client_id(self, client_order_id: str) -> Optional[Dict[str, Any]]:
        self._require_sdk()
        # MT5 uses "comment" field as client order marker
        from datetime import datetime, timedelta
        from_date = datetime.now() - timedelta(days=7)
        orders = _mt5_sdk.history_orders_get(from_date, datetime.now()) or []
        for o in orders:
            if str(getattr(o, "comment", "") or "") == str(client_order_id):
                return {
                    "id": str(o.ticket),
                    "symbol": o.symbol,
                    "comment": str(getattr(o, "comment", ""))
                }
        return None

    async def get_executions_by_client_id(self, client_order_id: str) -> List[Dict[str, Any]]:
        self._require_sdk()
        from datetime import datetime, timedelta
        from_date = datetime.now() - timedelta(days=7)
        deals = _mt5_sdk.history_deals_get(from_date, datetime.now()) or []
        return [
            {
                "id": str(d.ticket),
                "order": str(d.order),
                "symbol": d.symbol,
                "side": "BUY" if d.type == 0 else "SELL",
                "volume": d.volume,
                "price": d.price,
            }
            for d in deals
            if str(getattr(d, "comment", "") or "") == str(client_order_id)
        ]

    async def close_all_positions(self, symbol=None) -> list:
        positions = await self.get_open_positions()
        results = []
        for pos in positions or []:
            if symbol and str(pos.get("symbol") or "").upper() != str(symbol).upper():
                continue
            result = await self.close_position(str(pos["id"]))
            results.append(result)
        return results

    async def get_server_time(self) -> Optional[float]:
        if not _MT5_AVAILABLE:
            import time
            return float(time.time())
        tick = _mt5_sdk.symbol_info_tick(self.symbol)
        if tick is not None:
            return float(getattr(tick, "time", 0) or 0)
        import time
        return float(time.time())

    async def get_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        if not _MT5_AVAILABLE:
            return None
        tick = _mt5_sdk.symbol_info_tick(symbol)
        if tick is None:
            return None
        info = _mt5_sdk.symbol_info(symbol)
        pip_size = float(getattr(info, "point", 0.0001) or 0.0001) if info else 0.0001
        bid = float(getattr(tick, "bid", 0) or 0)
        ask = float(getattr(tick, "ask", 0) or 0)
        spread_pips = (ask - bid) / pip_size if pip_size > 0 else 0.0
        return {"symbol": symbol, "bid": bid, "ask": ask, "spread_pips": spread_pips}

