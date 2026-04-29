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
        self.provider_name = "bybit"
        self.mode = "demo" if testnet else "live"
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
            raise ConnectionError(self._normalize_error(resp, "bybit_connect_failed"))
        info_resp = self._session.get_instruments_info(category="linear", symbol=self.symbol)
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
        resp = self._session.get_wallet_balance(accountType="UNIFIED")
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
        if self._session is None:
            return OrderResult(order_id="", symbol=request.symbol, side=request.side, volume=float(request.volume), fill_price=float(request.price or 0.0), commission=0.0, success=False, error_message="Bybit session not connected")
        if not self._instrument_info:
            info_resp = self._session.get_instruments_info(category="linear", symbol=request.symbol)
            if info_resp.get("retCode") == 0 and info_resp.get("result", {}).get("list"):
                self._instrument_info = info_resp["result"]["list"][0]
        status = str(self._instrument_info.get("status") or "Trading")
        if status.lower() not in {"trading", "settling"}:
            return OrderResult(order_id="", symbol=request.symbol, side=request.side, volume=float(request.volume), fill_price=float(request.price or 0.0), commission=0.0, success=False, error_message=f"Bybit instrument not tradable: {status}")
        account = await self.get_account_info()
        if float(account.free_margin or 0.0) <= 0:
            return OrderResult(order_id="", symbol=request.symbol, side=request.side, volume=float(request.volume), fill_price=float(request.price or 0.0), commission=0.0, success=False, error_message="Bybit preflight failed: insufficient_available_balance")
        qty = self._normalize_qty(float(request.volume))
        link_id = str(request.comment or f"{request.symbol}-{int(__import__('time').time()*1000)}")[:36]
        resp = self._session.place_order(
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
            return OrderResult(order_id="", symbol=request.symbol, side=request.side, volume=float(qty), fill_price=float(request.price or 0.0), commission=0.0, success=False, error_message=self._normalize_error(resp, "bybit_order_failed"))
        order_id = str(resp["result"].get("orderId", ""))
        if not order_id:
            return OrderResult(order_id="", symbol=request.symbol, side=request.side, volume=float(qty), fill_price=float(request.price or 0.0), commission=0.0, success=False, error_message="bybit_order_failed:missing_order_id")
        fill_price = request.price or 0.0
        commission = 0.0
        # Verify submit state after broker acknowledgement.
        order_status = None
        try:
            verify_resp = self._session.get_open_orders(category="linear", symbol=request.symbol, orderId=order_id, limit=1)
            if verify_resp.get("retCode") == 0 and verify_resp.get("result", {}).get("list"):
                order_status = str(verify_resp["result"]["list"][0].get("orderStatus") or "")
        except Exception:
            order_status = None
        if order_status and order_status.lower() in {"rejected", "cancelled", "deactivated"}:
            return OrderResult(order_id=order_id, symbol=request.symbol, side=request.side, volume=float(qty), fill_price=float(fill_price or 0.0), commission=0.0, success=False, error_message=f"bybit_order_failed:status_{order_status}")
        exec_resp = self._session.get_executions(category="linear", orderId=order_id, limit=1)
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
        )

    async def close_position(self, position_id: str) -> OrderResult:
        self._require_sdk()
        positions = await self.get_open_positions()
        pos = next((p for p in positions if p.get("id") == position_id), None)
        if pos is None:
            return OrderResult(order_id=position_id, symbol="", side="close", volume=0.0, fill_price=0.0, commission=0.0, success=False, error_message=f"Position {position_id} not found")
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
            return OrderResult(order_id=position_id, symbol=pos["symbol"], side="close", volume=float(pos["volume"]), fill_price=0.0, commission=0.0, success=False, error_message=self._normalize_error(resp, "bybit_close_failed"))
        return OrderResult(order_id=resp["result"]["orderId"], symbol=pos["symbol"], side="close", volume=float(pos["volume"]), fill_price=0.0, commission=0.0, success=True)

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

    async def health_check(self) -> Dict[str, Any]:
        if not self._connected or self._session is None:
            return {"status": "disconnected", "reason": "provider_not_connected"}
        if not self.testnet and self.mode != "live":
            return {"status": "degraded", "reason": "provider_mode_mismatch"}
        if self.testnet and self.mode != "demo":
            return {"status": "degraded", "reason": "provider_mode_mismatch"}
        try:
            wallet = self._session.get_wallet_balance(accountType="UNIFIED")
            if wallet.get("retCode") != 0:
                return {"status": "auth_failed", "reason": self._normalize_error(wallet, "wallet_failed")}
            return {"status": "healthy", "reason": ""}
        except Exception as exc:
            return {"status": "degraded", "reason": self._normalize_error(exc, "health_check_failed")}

