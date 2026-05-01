"""MT5 session — wraps MetaTrader5 SDK calls.

All public methods are synchronous (the FastAPI endpoint handlers run them
in a thread executor so they do not block the asyncio event loop).
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import MetaTrader5 as _mt5  # type: ignore[import]

    _MT5_AVAILABLE = True
except ImportError:
    _mt5 = None  # type: ignore[assignment]
    _MT5_AVAILABLE = False


@dataclass
class MT5SessionConfig:
    login: int
    password: str
    server: str
    symbol: str = "EURUSD"
    timeframe: str = "M5"
    live: bool = False


_TF_MAP: Dict[str, int] = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 16385,
    "H4": 16388,
    "D1": 16408,
}


class MT5Session:
    """Thread-safe wrapper around the MetaTrader5 SDK."""

    def __init__(self, config: MT5SessionConfig) -> None:
        self._cfg = config
        self._lock = threading.Lock()
        self._connected = False

    def connect(self) -> None:
        if not _MT5_AVAILABLE:
            raise RuntimeError("MetaTrader5 package is not installed on this host")
        with self._lock:
            if not _mt5.initialize(
                login=self._cfg.login,
                password=self._cfg.password,
                server=self._cfg.server,
            ):
                raise ConnectionError(f"MT5 initialize failed: {_mt5.last_error()}")
            info = _mt5.account_info()
            if info is None:
                raise ConnectionError(f"MT5 account_info failed: {_mt5.last_error()}")
            self._connected = True
            logger.info(
                "MT5Session connected: login=%s server=%s live=%s",
                self._cfg.login,
                self._cfg.server,
                self._cfg.live,
            )

    def disconnect(self) -> None:
        with self._lock:
            if _MT5_AVAILABLE and self._connected:
                _mt5.shutdown()
            self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ── Account ─────────────────────────────────────────────────────────── #

    def get_account_info(self) -> Dict[str, Any]:
        self._require_connected()
        info = _mt5.account_info()
        if info is None:
            raise RuntimeError(f"MT5 account_info failed: {_mt5.last_error()}")
        return {
            "account_id": int(info.login),
            "balance": float(info.balance),
            "equity": float(info.equity),
            "margin": float(info.margin),
            "free_margin": float(info.margin_free),
            "margin_level": float(getattr(info, "margin_level", 0.0) or 0.0),
            "currency": str(getattr(info, "currency", "USD")),
            "leverage": int(getattr(info, "leverage", 1)),
        }

    # ── Candles ─────────────────────────────────────────────────────────── #

    def get_candles(self, symbol: str, timeframe: str, limit: int = 200) -> List[Dict[str, Any]]:
        self._require_connected()
        tf = _TF_MAP.get(timeframe.upper(), 5)
        if not _mt5.symbol_select(symbol, True):
            raise RuntimeError(f"MT5 symbol_select failed: {symbol}")
        rates = _mt5.copy_rates_from_pos(symbol, tf, 0, limit)
        if rates is None:
            raise RuntimeError(f"MT5 copy_rates_from_pos failed: {_mt5.last_error()}")
        return [
            {
                "timestamp": float(r["time"]),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": float(r["tick_volume"]),
            }
            for r in rates
        ]

    # ── Quote ────────────────────────────────────────────────────────────── #

    def get_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        self._require_connected()
        tick = _mt5.symbol_info_tick(symbol)
        if tick is None:
            return None
        sym_info = _mt5.symbol_info(symbol)
        pip_size = float(getattr(sym_info, "point", 0.0001) or 0.0001) if sym_info else 0.0001
        bid = float(getattr(tick, "bid", 0) or 0)
        ask = float(getattr(tick, "ask", 0) or 0)
        ts = float(getattr(tick, "time", 0) or 0)
        spread_pips = (ask - bid) / pip_size if pip_size > 0 else 0.0
        return {
            "symbol": symbol,
            "bid": bid,
            "ask": ask,
            "spread_pips": spread_pips,
            "timestamp": ts,
            "quote_id": f"mt5:{symbol}:{int(ts * 1000)}",
        }

    # ── Instrument spec ──────────────────────────────────────────────────── #

    def get_instrument_spec(self, symbol: str) -> Optional[Dict[str, Any]]:
        self._require_connected()
        info = _mt5.symbol_info(symbol)
        if info is None:
            return None
        return {
            "symbol": symbol,
            "min_lot": float(getattr(info, "volume_min", 0.01) or 0.01),
            "max_lot": float(getattr(info, "volume_max", 100.0) or 100.0),
            "lot_step": float(getattr(info, "volume_step", 0.01) or 0.01),
            "contract_size": float(getattr(info, "trade_contract_size", 100_000) or 100_000),
            "pip_size": float(getattr(info, "point", 0.0001) or 0.0001),
            "pip_value_per_lot": float(getattr(info, "trade_tick_value", 10.0) or 10.0),
            "margin_rate": float(getattr(info, "margin_initial", 0.01) or 0.01),
            "currency_base": str(getattr(info, "currency_base", "")),
            "currency_profit": str(getattr(info, "currency_profit", "")),
        }

    def estimate_margin(self, symbol: str, side: str, volume: float, price: float) -> float:
        self._require_connected()
        mt5_type = _mt5.ORDER_TYPE_BUY if side.lower() == "buy" else _mt5.ORDER_TYPE_SELL
        if hasattr(_mt5, "order_calc_margin"):
            try:
                result = _mt5.order_calc_margin(mt5_type, symbol, volume, price)
                if result is not None:
                    return float(result)
            except Exception:
                pass
        spec = self.get_instrument_spec(symbol)
        if spec:
            return volume * float(spec["contract_size"]) * price * float(spec["margin_rate"])
        return volume * price * 0.01

    # ── Orders ───────────────────────────────────────────────────────────── #

    def place_order(
        self,
        *,
        symbol: str,
        side: str,
        volume: float,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        comment: str = "",
    ) -> Dict[str, Any]:
        self._require_connected()
        sym_info = _mt5.symbol_info(symbol)
        if sym_info is None:
            raise RuntimeError(f"Symbol not found: {symbol}")
        tick = _mt5.symbol_info_tick(symbol)
        if tick is None:
            raise RuntimeError(f"No tick data for: {symbol}")

        order_type = _mt5.ORDER_TYPE_BUY if side.upper() == "BUY" else _mt5.ORDER_TYPE_SELL
        price = float(tick.ask if side.upper() == "BUY" else tick.bid)

        # Normalise volume
        vmin = float(getattr(sym_info, "volume_min", 0.01) or 0.01)
        vmax = float(getattr(sym_info, "volume_max", 100.0) or 100.0)
        vstep = float(getattr(sym_info, "volume_step", 0.01) or 0.01)
        vol = max(vmin, min(vmax, round(round(volume / vstep) * vstep, 8)))

        request: Dict[str, Any] = {
            "action": _mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": vol,
            "type": order_type,
            "price": price,
            "deviation": 20,
            "magic": 234000,
            "comment": comment[:31],  # MT5 limits comment to 31 chars
            "type_time": _mt5.ORDER_TIME_GTC,
            "type_filling": _mt5.ORDER_FILLING_IOC,
        }
        if stop_loss and float(stop_loss) > 0:
            request["sl"] = float(stop_loss)
        if take_profit and float(take_profit) > 0:
            request["tp"] = float(take_profit)

        result = _mt5.order_send(request)
        if result is None:
            raise RuntimeError(f"MT5 order_send returned None: {_mt5.last_error()}")
        if result.retcode not in (
            _mt5.TRADE_RETCODE_DONE,
            _mt5.TRADE_RETCODE_DONE_PARTIAL,
            _mt5.TRADE_RETCODE_PLACED,
        ):
            raise RuntimeError(f"MT5 order rejected: retcode={result.retcode} comment={result.comment}")
        return {
            "orderId": str(result.order),
            "positionId": str(result.deal),
            "executionPrice": float(result.price),
            "volume": float(result.volume),
            "retcode": int(result.retcode),
            "comment": str(result.comment),
        }

    def close_position(self, position_id: int) -> Dict[str, Any]:
        self._require_connected()
        positions = _mt5.positions_get(ticket=int(position_id))
        if not positions:
            raise RuntimeError(f"Position {position_id} not found")
        pos = positions[0]
        close_type = _mt5.ORDER_TYPE_SELL if pos.type == _mt5.ORDER_TYPE_BUY else _mt5.ORDER_TYPE_BUY
        tick = _mt5.symbol_info_tick(pos.symbol)
        if tick is None:
            raise RuntimeError(f"No tick data for {pos.symbol}")
        price = float(tick.bid if close_type == _mt5.ORDER_TYPE_SELL else tick.ask)
        request = {
            "action": _mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": close_type,
            "position": pos.ticket,
            "price": price,
            "deviation": 20,
            "magic": 234000,
            "type_time": _mt5.ORDER_TIME_GTC,
            "type_filling": _mt5.ORDER_FILLING_IOC,
        }
        result = _mt5.order_send(request)
        if result is None:
            raise RuntimeError(f"MT5 close order_send returned None: {_mt5.last_error()}")
        if result.retcode not in (
            _mt5.TRADE_RETCODE_DONE,
            _mt5.TRADE_RETCODE_DONE_PARTIAL,
        ):
            raise RuntimeError(f"MT5 close rejected: retcode={result.retcode}")
        return {
            "orderId": str(result.order),
            "executionPrice": float(result.price),
            "volume": float(result.volume),
        }

    def get_positions(self) -> List[Dict[str, Any]]:
        self._require_connected()
        positions = _mt5.positions_get() or []
        return [
            {
                "id": str(p.ticket),
                "symbol": p.symbol,
                "side": "BUY" if p.type == _mt5.ORDER_TYPE_BUY else "SELL",
                "volume": float(p.volume),
                "open_price": float(p.price_open),
                "sl": float(p.sl),
                "tp": float(p.tp),
                "profit": float(p.profit),
            }
            for p in positions
        ]

    def get_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        self._require_connected()
        from datetime import datetime, timedelta

        from_date = datetime.now() - timedelta(days=90)
        deals = _mt5.history_deals_get(from_date, datetime.now()) or []
        results = []
        for d in deals[-limit:]:
            results.append(
                {
                    "id": str(d.ticket),
                    "order": str(d.order),
                    "symbol": d.symbol,
                    "side": "BUY" if d.type == 0 else "SELL",
                    "volume": float(d.volume),
                    "price": float(d.price),
                    "profit": float(d.profit),
                    "time": float(d.time),
                    "comment": str(getattr(d, "comment", "") or ""),
                }
            )
        return results

    def get_order_by_client_id(self, client_order_id: str) -> Optional[Dict[str, Any]]:
        self._require_connected()
        from datetime import datetime, timedelta

        from_date = datetime.now() - timedelta(days=7)
        orders = _mt5.history_orders_get(from_date, datetime.now()) or []
        for o in orders:
            if str(getattr(o, "comment", "") or "") == str(client_order_id):
                return {
                    "id": str(o.ticket),
                    "symbol": o.symbol,
                    "comment": str(getattr(o, "comment", "")),
                }
        return None

    def get_executions_by_client_id(self, client_order_id: str) -> List[Dict[str, Any]]:
        self._require_connected()
        from datetime import datetime, timedelta

        from_date = datetime.now() - timedelta(days=7)
        deals = _mt5.history_deals_get(from_date, datetime.now()) or []
        return [
            {
                "id": str(d.ticket),
                "order": str(d.order),
                "symbol": d.symbol,
                "side": "BUY" if d.type == 0 else "SELL",
                "volume": float(d.volume),
                "price": float(d.price),
            }
            for d in deals
            if str(getattr(d, "comment", "") or "") == str(client_order_id)
        ]

    def health_check(self) -> Dict[str, Any]:
        if not self._connected or not _MT5_AVAILABLE:
            return {"status": "disconnected", "reason": "not_connected"}
        terminal_info = _mt5.terminal_info()
        if terminal_info is None or not bool(getattr(terminal_info, "connected", True)):
            return {"status": "degraded", "reason": "terminal_not_connected"}
        return {"status": "healthy", "reason": ""}

    def get_server_time(self) -> float:
        if not _MT5_AVAILABLE:
            return float(time.time())
        tick = _mt5.symbol_info_tick(self._cfg.symbol)
        if tick is not None:
            return float(getattr(tick, "time", 0) or 0)
        return float(time.time())

    # ── Internal helpers ─────────────────────────────────────────────────── #

    def _require_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("MT5Session is not connected. Call connect() first.")
