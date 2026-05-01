"""MT5BridgeProvider — HTTP client that communicates with the MT5 Bridge service.

Use this provider in Linux Docker containers instead of the native MT5Provider.
The MT5 Bridge (services/mt5-bridge/) runs on a Windows host with the
MetaTrader5 SDK installed and exposes its functionality over HTTP.

Configuration:
    MT5BridgeProvider(
        bridge_url="http://192.168.1.100:8181",
        api_key="your_bridge_api_key",         # Optional but recommended
        symbol="EURUSD",
        timeframe="M5",
        mode="live",                            # or "demo"
        _allow_live=True,                       # Required for live mode
    )
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import pandas as pd

from .base import AccountInfo, BrokerProvider, OrderRequest, OrderResult

logger = logging.getLogger(__name__)

try:
    import aiohttp as _aiohttp  # type: ignore[import]

    _AIOHTTP_AVAILABLE = True
except ImportError:
    _aiohttp = None  # type: ignore[assignment]
    _AIOHTTP_AVAILABLE = False


class MT5BridgeProvider(BrokerProvider):
    """BrokerProvider that delegates all MT5 calls to the HTTP bridge service.

    This enables running MT5 trading from a Linux Docker container by
    forwarding requests to a Windows host running services/mt5-bridge/.
    """

    def __init__(
        self,
        bridge_url: str,
        api_key: str = "",
        symbol: str = "EURUSD",
        timeframe: str = "M5",
        mode: str = "demo",
        _allow_live: bool = False,
    ) -> None:
        resolved_mode = str(mode or "demo").lower()
        if resolved_mode not in {"demo", "live"}:
            raise ValueError(f"MT5BridgeProvider mode must be demo|live, got: {resolved_mode}")
        if resolved_mode == "live" and not bool(_allow_live):
            raise ValueError(
                "MT5BridgeProvider is demo-only; pass _allow_live=True to enable live mode"
            )
        if not _AIOHTTP_AVAILABLE:
            raise RuntimeError(
                "MT5BridgeProvider requires the aiohttp package. Install: pip install aiohttp"
            )
        self.bridge_url = str(bridge_url).rstrip("/")
        self._api_key = str(api_key or "")
        self.symbol = symbol
        self.timeframe = timeframe
        self.provider_name = "mt5"
        self.mode = resolved_mode
        self._connected = False
        self._session: Optional[Any] = None  # aiohttp.ClientSession

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["x-api-key"] = self._api_key
        return headers

    async def _get(self, path: str, **params: Any) -> Any:
        if self._session is None:
            raise RuntimeError("MT5BridgeProvider session not initialised. Call connect() first.")
        url = f"{self.bridge_url}{path}"
        async with self._session.get(url, params=params, headers=self._headers()) as resp:
            if resp.status >= 400:
                text = await resp.text()
                raise RuntimeError(f"MT5Bridge GET {path} failed ({resp.status}): {text}")
            return await resp.json()

    async def _post(self, path: str, body: Any = None) -> Any:
        if self._session is None:
            raise RuntimeError("MT5BridgeProvider session not initialised. Call connect() first.")
        url = f"{self.bridge_url}{path}"
        async with self._session.post(url, json=body or {}, headers=self._headers()) as resp:
            if resp.status >= 400:
                text = await resp.text()
                raise RuntimeError(f"MT5Bridge POST {path} failed ({resp.status}): {text}")
            return await resp.json()

    async def connect(self) -> None:
        import aiohttp

        if self._session is None:
            self._session = aiohttp.ClientSession()
        try:
            result = await self._post("/connect")
            if not result.get("connected", False):
                raise ConnectionError("MT5Bridge /connect returned connected=false")
            self._connected = True
            logger.info("MT5BridgeProvider connected: %s → %s", self.symbol, self.bridge_url)
        except Exception as exc:
            self._connected = False
            if self.mode == "live":
                raise ConnectionError(f"MT5BridgeProvider live connect failed: {exc}") from exc
            logger.warning("MT5BridgeProvider connect failed (demo fallback): %s", exc)

    async def disconnect(self) -> None:
        if self._session is not None:
            try:
                await self._post("/disconnect")
            except Exception:
                pass
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None
        self._connected = False
        logger.info("MT5BridgeProvider disconnected")

    async def get_account_info(self) -> AccountInfo:
        info = await self._get("/account")
        return AccountInfo(
            balance=float(info.get("balance", 0)),
            equity=float(info.get("equity", 0)),
            margin=float(info.get("margin", 0)),
            free_margin=float(info.get("free_margin", 0)),
            margin_level=float(info.get("margin_level", 0)),
            currency=str(info.get("currency", "USD")),
        )

    async def get_candles(self, symbol: str, timeframe: str, limit: int = 200) -> pd.DataFrame:
        bars = await self._get("/candles", symbol=symbol, timeframe=timeframe, limit=limit)
        if not bars:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = pd.DataFrame(bars)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
        df = df.set_index("timestamp").sort_index()
        df = df.rename(columns={"volume": "volume"})
        for col in ("open", "high", "low", "close", "volume"):
            if col in df.columns:
                df[col] = df[col].astype(float)
        return df

    async def place_order(self, request: OrderRequest) -> OrderResult:
        if self.mode == "live" and not getattr(request, "client_order_id", None):
            return OrderResult(
                order_id="", symbol=request.symbol, side=request.side,
                volume=request.volume, fill_price=0.0, commission=0.0,
                success=False, error_message="mt5bridge_live_requires_client_order_id",
                submit_status="REJECTED", fill_status="UNKNOWN",
            )
        try:
            result = await self._post(
                "/order",
                {
                    "symbol": request.symbol,
                    "side": request.side,
                    "volume": request.volume,
                    "stop_loss": request.stop_loss,
                    "take_profit": request.take_profit,
                    "comment": str(getattr(request, "client_order_id", "") or request.comment or ""),
                },
            )
        except Exception as exc:
            return OrderResult(
                order_id="", symbol=request.symbol, side=request.side,
                volume=request.volume, fill_price=float(request.price or 0.0),
                commission=0.0, success=False,
                error_message=f"mt5bridge_order_failed:{exc}",
                submit_status="UNKNOWN", fill_status="UNKNOWN",
            )
        order_id = str(result.get("orderId") or "")
        fill_price = float(result.get("executionPrice") or 0.0)
        if not order_id or fill_price <= 0:
            return OrderResult(
                order_id=order_id, symbol=request.symbol, side=request.side,
                volume=request.volume, fill_price=fill_price,
                commission=0.0, success=False,
                error_message="mt5bridge_order:missing_order_id_or_price",
                submit_status="ACKED" if order_id else "REJECTED",
                fill_status="UNKNOWN", raw_response=result,
            )
        return OrderResult(
            order_id=order_id, symbol=request.symbol, side=request.side,
            volume=float(result.get("volume", request.volume)),
            fill_price=fill_price, commission=0.0, success=True,
            submit_status="ACKED", fill_status="FILLED",
            broker_deal_id=str(result.get("positionId") or "") or None,
            raw_response=result,
        )

    async def close_position(self, position_id: str) -> OrderResult:
        try:
            result = await self._post("/close_position", {"position_id": int(position_id)})
            return OrderResult(
                order_id=str(result.get("orderId", position_id)),
                symbol="", side="close",
                volume=float(result.get("volume", 0)),
                fill_price=float(result.get("executionPrice", 0)),
                commission=0.0, success=True,
            )
        except Exception as exc:
            return OrderResult(
                order_id=position_id, symbol="", side="close",
                volume=0, fill_price=0, commission=0, success=False,
                error_message=f"mt5bridge_close_failed:{exc}",
            )

    async def get_open_positions(self) -> List[Dict[str, Any]]:
        return await self._get("/positions")

    async def get_trade_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        return await self._get("/history", limit=limit)

    async def health_check(self) -> Dict[str, Any]:
        try:
            return await self._get("/health")
        except Exception as exc:
            return {"status": "degraded", "reason": f"bridge_health_check_failed:{exc}"}

    async def get_instrument_spec(self, symbol: str) -> Optional[Dict[str, Any]]:
        result = await self._get("/instrument_spec", symbol=symbol)
        return result if isinstance(result, dict) else None

    async def estimate_margin(self, symbol: str, side: str, volume: float, price: float) -> float:
        result = await self._get(
            "/estimate_margin", symbol=symbol, side=side, volume=volume, price=price
        )
        return float(result.get("margin", 0.0))

    async def get_order_by_client_id(self, client_order_id: str) -> Optional[Dict[str, Any]]:
        result = await self._get("/order_by_client_id", client_order_id=client_order_id)
        return result if isinstance(result, dict) else None

    async def get_executions_by_client_id(self, client_order_id: str) -> List[Dict[str, Any]]:
        result = await self._get(
            "/executions_by_client_id", client_order_id=client_order_id
        )
        return list(result) if isinstance(result, list) else []

    async def close_all_positions(self, symbol: Optional[str] = None) -> List[Any]:
        positions = await self.get_open_positions()
        results = []
        for pos in positions or []:
            if symbol and str(pos.get("symbol") or "").upper() != str(symbol).upper():
                continue
            result = await self.close_position(str(pos.get("id", "")))
            results.append(result)
        return results

    async def get_server_time(self) -> Optional[float]:
        try:
            result = await self._get("/server_time")
            return float(result.get("server_time", time.time()))
        except Exception:
            return float(time.time())

    async def get_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        try:
            result = await self._get("/quote", symbol=symbol)
            return result if isinstance(result, dict) else None
        except Exception:
            return None

    @property
    def supports_client_order_id(self) -> bool:
        return True

    @property
    def client_order_id_transport(self) -> str:
        return "comment"
