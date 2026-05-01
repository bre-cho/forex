"""Bybit provider — optional real adapter, fails closed when SDK absent.

Requires ``pybit`` package (pip install pybit).
In live mode without the package the provider refuses to connect.

All pybit HTTP SDK calls are blocking (synchronous).  They are run in a
thread via ``asyncio.to_thread`` so the asyncio event loop is never blocked.
"""
from __future__ import annotations

import asyncio
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
        mode: str | None = None,
        _allow_live: bool = False,
    ) -> None:
        resolved_mode = str(mode or ("demo" if bool(testnet) else "live")).lower()
        if resolved_mode not in {"demo", "live"}:
            raise ValueError(f"BybitProvider mode must be demo|live, got: {resolved_mode}")
        if resolved_mode == "live" and not bool(_allow_live):
            raise ValueError("BybitProvider is demo-only; use BybitLiveProvider for live mode")
        self.api_key = api_key
        self.api_secret = api_secret
        self.symbol = symbol
        self.timeframe = timeframe
        self.testnet = resolved_mode != "live"
        self.provider_name = "bybit"
        self.mode = resolved_mode
        self._session: Optional[Any] = None
        self._connected = False
        self._instrument_info: dict[str, Any] = {}

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _require_sdk(self) -> None:
        if not _BYBIT_AVAILABLE:
            raise RuntimeError(
                "BybitProvider requires the pybit package. Install: pip install pybit"
            )

    async def _sdk(self, fn: Any, /, **kwargs: Any) -> Any:
        """Run a blocking pybit SDK method in a thread to avoid blocking the event loop."""
        return await asyncio.to_thread(fn, **kwargs)

    def _normalize_qty(self, qty: float) -> float:
        info = self._instrument_info or {}
        lot = info.get("lotSizeFilter", {}) if isinstance(info, dict) else {}
        min_qty = float(lot.get("minOrderQty", 0.001) or 0.001)
        step = float(lot.get("qtyStep", min_qty) or min_qty)
        normalized = round(round(qty / step) * step, 8)
        return max(min_qty, normalized)

    def _normalize_error(self, payload: Any, default: str = "bybit_error") -> str:
        if isinstance(payload, BaseException):
            return f"{default}:{payload}"
        if isinstance(payload, dict):
            code = payload.get("retCode")
            msg = payload.get("retMsg") or payload.get("ret_msg") or payload.get("msg")
            if code is None and msg is None:
                return default
            return f"{default}:{code}:{msg}"
        return f"{default}:{payload}"

    def _ensure_live_key_not_testnet_like(self) -> None:
        if self.testnet:
            return
        key = str(self.api_key or "").lower()
        if key.startswith("test") or "testnet" in key:
            raise RuntimeError("Bybit live mode rejected testnet-like api key")

    async def connect(self) -> None:
        if not self.testnet:
            self._require_sdk()
            self._ensure_live_key_not_testnet_like()
        if not _BYBIT_AVAILABLE:
            # SDK is required for both demo and live modes.  Use PaperProvider
            # when running without a real broker connection.
            raise RuntimeError(
                "BybitProvider requires the pybit package (pip install pybit). "
                "Use PaperProvider for offline simulation."
            )
        self._session = _BybitHTTP(
            testnet=self.testnet,
            api_key=self.api_key,
            api_secret=self.api_secret,
        )
        resp = await self._sdk(self._session.get_wallet_balance, accountType="UNIFIED")
        if resp.get("retCode") != 0:
            raise ConnectionError(self._normalize_error(resp, "bybit_connect_failed"))
        info_resp = await self._sdk(
            self._session.get_instruments_info, category="linear", symbol=self.symbol
        )
        if info_resp.get("retCode") == 0 and info_resp.get("result", {}).get("list"):
            self._instrument_info = info_resp["result"]["list"][0]
        if not self._instrument_info:
            raise ConnectionError("bybit_connect_failed:instrument_info_missing")
        self._connected = True
        logger.info("BybitProvider connected: symbol=%s testnet=%s", self.symbol, self.testnet)

    async def disconnect(self) -> None:
        self._session = None
        self._connected = False

    async def get_account_info(self) -> AccountInfo:
        self._require_sdk()
        resp = await self._sdk(self._session.get_wallet_balance, accountType="UNIFIED")
        if resp.get("retCode") != 0:
            raise RuntimeError(self._normalize_error(resp, "bybit_wallet_failed"))
        wallet = (resp.get("result", {}).get("list") or [{}])[0]
        coins = wallet.get("coin") or []
        usdt = next((c for c in coins if c.get("coin") == "USDT"), {})
        equity = float(wallet.get("totalEquity") or usdt.get("equity") or 0)
        avail = float(wallet.get("totalAvailableBalance") or usdt.get("availableBalance") or usdt.get("walletBalance") or 0)
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
        resp = await self._sdk(
            self._session.get_kline, category="linear", symbol=symbol, interval=interval, limit=limit
        )
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
        if self._session is None:
            return OrderResult(order_id="", symbol=request.symbol, side=request.side, volume=float(request.volume), fill_price=float(request.price or 0.0), commission=0.0, success=False, error_message="Bybit session not connected", submit_status="UNKNOWN", fill_status="UNKNOWN")
        if not self._instrument_info:
            info_resp = await self._sdk(
                self._session.get_instruments_info, category="linear", symbol=request.symbol
            )
            if info_resp.get("retCode") == 0 and info_resp.get("result", {}).get("list"):
                self._instrument_info = info_resp["result"]["list"][0]
        status = str(self._instrument_info.get("status") or "Trading")
        if status.lower() not in {"trading", "settling"}:
            return OrderResult(order_id="", symbol=request.symbol, side=request.side, volume=float(request.volume), fill_price=float(request.price or 0.0), commission=0.0, success=False, error_message=f"Bybit instrument not tradable: {status}", submit_status="REJECTED", fill_status="UNKNOWN")
        account = await self.get_account_info()
        if float(account.free_margin or 0.0) <= 0:
            return OrderResult(order_id="", symbol=request.symbol, side=request.side, volume=float(request.volume), fill_price=float(request.price or 0.0), commission=0.0, success=False, error_message="Bybit preflight failed: insufficient_available_balance", submit_status="REJECTED", fill_status="UNKNOWN")
        qty = self._normalize_qty(float(request.volume))
        link_id = str(request.comment or f"{request.symbol}-{int(__import__('time').time()*1000)}")[:36]
        resp = await self._sdk(
            self._session.place_order,
            category="linear",
            symbol=request.symbol,
            side="Buy" if request.side.lower() == "buy" else "Sell",
            orderType="Market",
            qty=str(qty),
            stopLoss=str(request.stop_loss) if request.stop_loss else None,
            takeProfit=str(request.take_profit) if request.take_profit else None,
            orderLinkId=link_id,
        )
        if resp.get("retCode") != 0:
            return OrderResult(order_id="", symbol=request.symbol, side=request.side, volume=float(qty), fill_price=float(request.price or 0.0), commission=0.0, success=False, error_message=self._normalize_error(resp, "bybit_order_failed"), submit_status="REJECTED", fill_status="UNKNOWN", raw_response=dict(resp))
        order_id = str(resp["result"].get("orderId", ""))
        if not order_id:
            return OrderResult(order_id="", symbol=request.symbol, side=request.side, volume=float(qty), fill_price=float(request.price or 0.0), commission=0.0, success=False, error_message="bybit_order_failed:missing_order_id", submit_status="REJECTED", fill_status="UNKNOWN", raw_response=dict(resp))
        fill_price = request.price or 0.0
        commission = 0.0
        # Verify submit state after broker acknowledgement.
        verify_resp: dict = {}
        order_status = None
        try:
            verify_resp = await self._sdk(
                self._session.get_open_orders,
                category="linear", symbol=request.symbol, orderId=order_id, limit=1,
            )
            if verify_resp.get("retCode") == 0 and verify_resp.get("result", {}).get("list"):
                order_status = str(verify_resp["result"]["list"][0].get("orderStatus") or "")
        except Exception:
            order_status = None
        if order_status and order_status.lower() in {"rejected", "cancelled", "deactivated"}:
            return OrderResult(order_id=order_id, symbol=request.symbol, side=request.side, volume=float(qty), fill_price=float(fill_price or 0.0), commission=0.0, success=False, error_message=f"bybit_order_failed:status_{order_status}", submit_status="REJECTED", fill_status="UNKNOWN", raw_response={"place": dict(resp), "verify": verify_resp})
        exec_resp = await self._sdk(
            self._session.get_executions, category="linear", orderId=order_id, limit=1
        )
        if exec_resp.get("retCode") == 0 and exec_resp.get("result", {}).get("list"):
            exec_row = exec_resp["result"]["list"][0]
            fill_price = float(exec_row.get("execPrice", fill_price))
            commission = float(exec_row.get("execFee") or 0.0)
        return OrderResult(
            success=True,
            order_id=order_id,
            symbol=request.symbol,
            side=request.side,
            volume=qty,
            fill_price=fill_price,
            commission=commission,
            submit_status="ACKED",
            fill_status="FILLED" if fill_price and float(fill_price) > 0 else "PENDING",
            broker_deal_id=str((exec_resp.get("result", {}).get("list") or [{}])[0].get("execId") or "") or None,
            raw_response={"place": dict(resp), "verify": verify_resp, "executions": dict(exec_resp)},
        )

    async def close_position(self, position_id: str) -> OrderResult:
        self._require_sdk()
        positions = await self.get_open_positions()
        pos = next((p for p in positions if p.get("id") == position_id), None)
        if pos is None:
            return OrderResult(order_id=position_id, symbol="", side="close", volume=0.0, fill_price=0.0, commission=0.0, success=False, error_message=f"Position {position_id} not found")
        close_side = "Sell" if pos["side"] == "BUY" else "Buy"
        resp = await self._sdk(
            self._session.place_order,
            category="linear",
            symbol=pos["symbol"],
            side=close_side,
            orderType="Market",
            qty=str(pos["volume"]),
            reduceOnly=True,
        )
        if resp.get("retCode") != 0:
            return OrderResult(order_id=position_id, symbol=pos["symbol"], side="close", volume=float(pos["volume"]), fill_price=0.0, commission=0.0, success=False, error_message=self._normalize_error(resp, "bybit_close_failed"))
        return OrderResult(order_id=resp["result"]["orderId"], symbol=pos["symbol"], side="close", volume=float(pos["volume"]), fill_price=0.0, commission=0.0, success=True)

    async def get_open_positions(self) -> List[Dict[str, Any]]:
        self._require_sdk()
        resp = await self._sdk(self._session.get_positions, category="linear", symbol=self.symbol)
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
        resp = await self._sdk(self._session.get_executions, category="linear", limit=limit)
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

    async def health_check(self) -> Dict[str, Any]:
        if not self._connected or self._session is None:
            return {"status": "disconnected", "reason": "provider_not_connected"}
        if not self.testnet and self.mode != "live":
            return {"status": "degraded", "reason": "provider_mode_mismatch"}
        if self.testnet and self.mode != "demo":
            return {"status": "degraded", "reason": "provider_mode_mismatch"}
        try:
            wallet = await self._sdk(self._session.get_wallet_balance, accountType="UNIFIED")
            if wallet.get("retCode") != 0:
                return {"status": "auth_failed", "reason": self._normalize_error(wallet, "wallet_failed")}
            return {"status": "healthy", "reason": ""}
        except Exception as exc:
            return {"status": "degraded", "reason": self._normalize_error(exc, "health_check_failed")}

    @property
    def supports_client_order_id(self) -> bool:
        return True

    @property
    def client_order_id_transport(self) -> str:
        return "orderLinkId"

    async def get_instrument_spec(self, symbol: str) -> Optional[Dict[str, Any]]:
        self._require_sdk()
        resp = await self._sdk(self._session.get_instruments_info, category="linear", symbol=symbol)
        if resp.get("retCode") == 0 and resp.get("result", {}).get("list"):
            info = resp["result"]["list"][0]
            lot = info.get("lotSizeFilter", {})
            price_filter = info.get("priceFilter", {})
            return {
                "symbol": symbol,
                "min_lot": float(lot.get("minOrderQty", 0.001) or 0.001),
                "max_lot": float(lot.get("maxOrderQty", 1000.0) or 1000.0),
                "lot_step": float(lot.get("qtyStep", 0.001) or 0.001),
                "tick_size": float(price_filter.get("tickSize", 0.01) or 0.01),
                "contract_size": 1.0,
                "margin_rate": float(info.get("leverage", {}).get("leverageBuy", "10") or "10") and 1.0 / float(info.get("leverage", {}).get("leverageBuy", "10") or "10"),
                "pip_size": float(price_filter.get("tickSize", 0.01) or 0.01),
                "pip_value_per_lot": 1.0,
                "raw": info,
            }
        return None

    async def estimate_margin(self, symbol: str, side: str, volume: float, price: float) -> float:
        spec = await self.get_instrument_spec(symbol)
        if spec:
            margin_rate = float(spec.get("margin_rate", 0.1) or 0.1)
            contract_size = float(spec.get("contract_size", 1.0) or 1.0)
            return volume * contract_size * price * margin_rate
        return volume * price * 0.1

    async def get_order_by_client_id(self, client_order_id: str) -> Optional[Dict[str, Any]]:
        self._require_sdk()
        try:
            resp = await self._sdk(
                self._session.get_open_orders, category="linear", orderLinkId=client_order_id, limit=1
            )
            if resp.get("retCode") == 0 and resp.get("result", {}).get("list"):
                return dict(resp["result"]["list"][0])
            resp = await self._sdk(
                self._session.get_order_history, category="linear", orderLinkId=client_order_id, limit=1
            )
            if resp.get("retCode") == 0 and resp.get("result", {}).get("list"):
                return dict(resp["result"]["list"][0])
        except Exception:
            pass
        return None

    async def get_executions_by_client_id(self, client_order_id: str) -> List[Dict[str, Any]]:
        self._require_sdk()
        order = await self.get_order_by_client_id(client_order_id)
        if order:
            order_id = order.get("orderId")
            if order_id:
                try:
                    resp = await self._sdk(
                        self._session.get_executions, category="linear", orderId=order_id, limit=50
                    )
                    if resp.get("retCode") == 0 and resp.get("result", {}).get("list"):
                        return [dict(r) for r in resp["result"]["list"]]
                except Exception:
                    pass
        return []

    async def close_all_positions(self, symbol=None) -> List[Any]:
        positions = await self.get_open_positions()
        results = []
        for pos in positions or []:
            if symbol and str(pos.get("symbol") or "").upper() != str(symbol).upper():
                continue
            result = await self.close_position(str(pos.get("id", "")))
            results.append(result)
        return results

    async def get_server_time(self) -> Optional[float]:
        self._require_sdk()
        try:
            resp = await self._sdk(self._session.get_server_time)
            if resp.get("retCode") == 0:
                ts = resp.get("result", {}).get("timeSecond") or resp.get("result", {}).get("timeNano")
                if ts:
                    return float(ts) if float(ts) < 1e12 else float(ts) / 1e9
        except Exception:
            pass
        import time
        return float(time.time())

    async def get_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        self._require_sdk()
        try:
            resp = await self._sdk(self._session.get_tickers, category="linear", symbol=symbol)
            if resp.get("retCode") == 0 and resp.get("result", {}).get("list"):
                t = resp["result"]["list"][0]
                bid = float(t.get("bid1Price") or t.get("lastPrice") or 0)
                ask = float(t.get("ask1Price") or t.get("lastPrice") or 0)
                spec = self._instrument_info.get("priceFilter", {})
                tick = float(spec.get("tickSize", 0.01) or 0.01)
                spread_pips = (ask - bid) / tick if tick > 0 else 0.0
                ts_ms = float(t.get("time") or resp.get("time") or 0)
                ts = ts_ms / 1000.0 if ts_ms > 0 else 0.0
                quote_id = str(t.get("bid1Price") or "") + ":" + str(t.get("ask1Price") or "") + ":" + str(int(ts_ms or 0))
                return {
                    "symbol": symbol,
                    "bid": bid,
                    "ask": ask,
                    "spread_pips": spread_pips,
                    "timestamp": ts,
                    "quote_id": f"bybit:{symbol}:{quote_id}",
                }
        except Exception:
            pass
        return None

