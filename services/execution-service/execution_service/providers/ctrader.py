"""cTrader broker provider — wraps the ctrader_provider engine."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import pandas as pd

from .base import AccountInfo, BrokerProvider, OrderRequest, OrderResult
from .ctrader_execution_adapter import (
    CTraderUnavailableExecutionAdapter,
    build_execution_adapter,
)
from .ctrader_market_data_adapter import CTraderMarketDataAdapter

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
        *,
        _allow_live_override: bool = False,
        token_expires_in: int = 3600,
    ) -> None:
        if bool(live) and not bool(_allow_live_override):
            raise ValueError("CTraderProvider is demo-only; use CTraderLiveProvider for live mode")
        self._client_id = client_id
        self._client_secret = client_secret
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._account_id = account_id
        self.symbol = symbol
        self.timeframe = timeframe
        self.provider_name = "ctrader"
        self.live = bool(live)
        self._provider = None
        self._execution_adapter = CTraderUnavailableExecutionAdapter("execution_adapter_not_initialized")
        self._market_data_adapter = CTraderMarketDataAdapter(None)
        self._connected = False
        # Token auto-refresh (initialised lazily on connect)
        self._token_refresher = None
        self._token_expires_in = int(token_expires_in or 3600)

    # ------------------------------------------------------------------
    # Token refresh callbacks (called by CTraderTokenRefresher)
    # ------------------------------------------------------------------

    async def _on_token_refreshed(self, access_token: str, refresh_token: str, expires_in: int) -> None:
        """Update stored credentials and notify the underlying provider."""
        self._access_token = access_token
        self._refresh_token = refresh_token
        if self._token_refresher is not None:
            self._token_refresher.update_refresh_token(refresh_token, expires_in)
        # Propagate to the live underlying provider if it supports credential update
        if self._provider is not None:
            update_fn = getattr(self._provider, "update_credentials", None)
            if callable(update_fn):
                try:
                    update_fn(access_token=access_token, refresh_token=refresh_token)
                except Exception as exc:
                    logger.warning("CTraderProvider: provider credential update failed: %s", exc)
        logger.info("CTraderProvider: OAuth2 token refreshed (expires_in=%ds)", expires_in)

    async def _on_refresh_failed(self, reason: str) -> None:
        """Mark provider degraded when token refresh exhausts all retries."""
        logger.error("CTraderProvider: token refresh permanently failed: %s — pausing provider", reason)
        self._connected = False

    def _start_token_refresher(self) -> None:
        """Initialise and start the background token refresh task."""
        try:
            from execution_service.ctrader_token_refresher import CTraderTokenRefresher
        except ImportError:
            logger.warning("CTraderTokenRefresher not available; token will not auto-refresh")
            return
        self._token_refresher = CTraderTokenRefresher(
            client_id=self._client_id,
            client_secret=self._client_secret,
            refresh_token=self._refresh_token,
            on_token_refreshed=self._on_token_refreshed,
            on_refresh_failed=self._on_refresh_failed,
        )
        import asyncio
        asyncio.ensure_future(
            self._token_refresher.start(expires_in_seconds=self._token_expires_in),
        )

    async def connect(self) -> None:
        try:
            from trading_core.engines.ctrader_provider import CTraderDataProvider

            self._provider = CTraderDataProvider(
                symbol=self.symbol,
                timeframe=self.timeframe,
            )
            self._market_data_adapter = CTraderMarketDataAdapter(self._provider)
            self._execution_adapter = build_execution_adapter(self._provider)
            # Live mode must verify account + initial market stream readiness before connected=true
            if self.live:
                if not self._account_id:
                    raise RuntimeError("CTrader live account_id missing")
                if not self._execution_adapter.available:
                    raise RuntimeError("CTrader live execution adapter unavailable")
                info = await self._execution_adapter.get_account_info()
                adapter_account = info.get("account_id") or info.get("accountId")
                if adapter_account is not None and int(adapter_account) != int(self._account_id):
                    raise RuntimeError("CTrader live account authorization mismatch")
                candles = self._market_data_adapter.get_candles(limit=1)
                if candles is None or candles.empty:
                    raise RuntimeError("CTrader live stream not ready")
            self._connected = True
            # Start token auto-refresh after successful connection
            self._start_token_refresher()
            logger.info("CTraderProvider connected: %s", self.symbol)
        except ImportError:
            if self.live:
                # P3: live mode must fail closed — stub is not acceptable
                raise RuntimeError(
                    "CTraderProvider live mode requires trading_core to be installed. "
                    "Ensure trading_core.engines.ctrader_provider is available."
                )
            logger.warning("trading_core not available; CTraderProvider running in stub/paper mode only")
            self._execution_adapter = CTraderUnavailableExecutionAdapter("trading_core_not_available")
            self._market_data_adapter = CTraderMarketDataAdapter(None)
            self._connected = False

    def _normalize_error(self, err: Any) -> str:
        if isinstance(err, BaseException):
            return f"ctrader_error:{err}"
        if isinstance(err, dict):
            code = err.get("code") or err.get("errorCode") or "unknown"
            msg = err.get("message") or err.get("errorMessage") or "unknown"
            return f"ctrader_error:{code}:{msg}"
        return f"ctrader_error:{err}"

    async def disconnect(self) -> None:
        if self._token_refresher is not None:
            try:
                await self._token_refresher.stop()
            except Exception as exc:
                logger.warning("CTraderProvider: token refresher stop failed: %s", exc)
            self._token_refresher = None
        self._connected = False
        logger.info("CTraderProvider disconnected")

    async def get_account_info(self) -> AccountInfo:
        if self._execution_adapter.available:
            info = await self._execution_adapter.get_account_info()
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
        return self._market_data_adapter.get_candles(limit=limit)

    async def place_order(self, request: OrderRequest) -> OrderResult:
        # P0.4: live mode requires client_order_id
        if self.live and not str(getattr(request, "client_order_id", "") or ""):
            return OrderResult(
                order_id="",
                symbol=request.symbol,
                side=request.side,
                volume=request.volume,
                fill_price=0.0,
                commission=0.0,
                success=False,
                error_message="ctrader_live_requires_client_order_id",
                submit_status="REJECTED",
                fill_status="UNKNOWN",
            )
        if self._execution_adapter.available:
            try:
                result = await self._execution_adapter.place_market_order(
                    symbol=request.symbol,
                    side=request.side,
                    volume=request.volume,
                    stop_loss=request.stop_loss,
                    take_profit=request.take_profit,
                    comment=request.comment,
                )
            except Exception as exc:
                return OrderResult(
                    order_id="",
                    symbol=request.symbol,
                    side=request.side,
                    volume=request.volume,
                    fill_price=float(request.price or 0.0),
                    commission=0.0,
                    success=False,
                    error_message=self._normalize_error(exc),
                    submit_status="UNKNOWN",
                    fill_status="UNKNOWN",
                )
            broker_order_id = str(result.get("orderId") or result.get("positionId") or "")
            execution_price = float(result.get("executionPrice") or result.get("fillPrice") or 0.0)
            if not broker_order_id:
                return OrderResult(
                    order_id="",
                    symbol=request.symbol,
                    side=request.side,
                    volume=request.volume,
                    fill_price=execution_price,
                    commission=float(result.get("commission") or 0.0),
                    success=False,
                    error_message="ctrader_error:missing_order_id",
                    submit_status="REJECTED",
                    fill_status="UNKNOWN",
                    raw_response=dict(result),
                )
            if execution_price <= 0:
                return OrderResult(
                    order_id=broker_order_id,
                    symbol=request.symbol,
                    side=request.side,
                    volume=request.volume,
                    fill_price=0.0,
                    commission=float(result.get("commission") or 0.0),
                    success=False,
                    error_message="ctrader_error:invalid_execution_price",
                    submit_status="ACKED",
                    fill_status="UNKNOWN",
                    raw_response=dict(result),
                )
            return OrderResult(
                order_id=broker_order_id,
                symbol=request.symbol,
                side=request.side,
                volume=request.volume,
                fill_price=execution_price,
                commission=float(result.get("commission") or 0.0),
                success=True,
                submit_status="ACKED",
                fill_status="FILLED",
                broker_position_id=str(result.get("positionId") or "") or None,
                broker_deal_id=str(result.get("dealId") or "") or None,
                raw_response=dict(result),
            )
        return OrderResult(
            order_id="", symbol=request.symbol, side=request.side,
            volume=request.volume, fill_price=0, commission=0,
            success=False, error_message="Provider not connected",
            submit_status="UNKNOWN", fill_status="UNKNOWN",
        )

    async def close_position(self, position_id: str) -> OrderResult:
        if self._execution_adapter.available:
            result = await self._execution_adapter.close_position(position_id=int(position_id))
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
        if self._execution_adapter.available:
            return await self._execution_adapter.get_positions()
        return []

    async def get_trade_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        if self._execution_adapter.available:
            return await self._execution_adapter.get_history(limit=limit)
        return []

    async def health_check(self) -> Dict[str, Any]:
        if not self._connected:
            return {"status": "disconnected", "reason": "provider_not_connected"}
        if self.live and not self._account_id:
            return {"status": "degraded", "reason": "live_account_id_missing"}
        execution_health = await self._execution_adapter.health_check()
        if execution_health.status != "healthy":
            return {"status": execution_health.status, "reason": execution_health.reason}
        market_health = self._market_data_adapter.health_check()
        if market_health.status != "healthy":
            return {"status": market_health.status, "reason": market_health.reason}
        return {"status": "healthy", "reason": ""}

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def mode(self) -> str:
        if not self._connected:
            return "unavailable"
        if not self._execution_adapter.available:
            return "stub"
        return "live" if self.live else "demo"

    @property
    def supports_client_order_id(self) -> bool:
        return True

    # ------------------------------------------------------------------
    # Live-required broker contract methods
    # ------------------------------------------------------------------

    async def get_instrument_spec(self, symbol: str):
        """Return instrument spec dict from the underlying cTrader provider."""
        if self._execution_adapter.available:
            result = await self._execution_adapter.get_instrument_spec(symbol)
            if result is not None:
                return result
        if self.live:
            raise RuntimeError(
                f"ctrader_get_instrument_spec_unavailable:{symbol} — "
                "ensure the underlying engine implements get_instrument_spec()"
            )
        return None

    async def estimate_margin(self, symbol: str, side: str, volume: float, price: float) -> float:
        """Estimate margin using broker-native calculation if available."""
        if self._execution_adapter.available:
            result = await self._execution_adapter.estimate_margin(
                symbol=symbol, side=side, volume=volume, price=price
            )
            if result > 0:
                return result
        if self.live:
            raise RuntimeError(
                f"ctrader_estimate_margin_unavailable:{symbol} — "
                "ensure the underlying engine implements estimate_margin()"
            )
        return 0.0

    async def get_order_by_client_id(self, client_order_id: str):
        """Look up a pending/historical order by clientMsgId/comment."""
        if self._execution_adapter.available:
            fn = getattr(self._provider, "get_order_by_client_id", None)
            if callable(fn):
                try:
                    import inspect
                    result = fn(client_order_id)
                    if inspect.isawaitable(result):
                        result = await result
                    return dict(result) if result else None
                except Exception as exc:
                    if self.live:
                        raise RuntimeError(f"ctrader_get_order_by_client_id_failed:{exc}") from exc
            # Fallback: search history
            try:
                history = await self._execution_adapter.get_history(limit=500)
                for trade in history or []:
                    comment = str(trade.get("comment") or trade.get("clientMsgId") or "")
                    if comment == str(client_order_id):
                        return dict(trade)
            except Exception as exc:
                if self.live:
                    raise RuntimeError(f"ctrader_history_lookup_failed:{exc}") from exc
        return None

    async def get_executions_by_client_id(self, client_order_id: str):
        """Return deals/executions matching a client order id."""
        if self._execution_adapter.available:
            fn = getattr(self._provider, "get_executions_by_client_id", None)
            if callable(fn):
                try:
                    import inspect
                    result = fn(client_order_id)
                    if inspect.isawaitable(result):
                        result = await result
                    return [dict(r) for r in (result or [])]
                except Exception as exc:
                    if self.live:
                        raise RuntimeError(f"ctrader_get_executions_by_client_id_failed:{exc}") from exc
            try:
                history = await self._execution_adapter.get_history(limit=500)
                return [
                    dict(t) for t in (history or [])
                    if str(t.get("comment") or t.get("clientMsgId") or "") == str(client_order_id)
                ]
            except Exception as exc:
                if self.live:
                    raise RuntimeError(f"ctrader_execution_history_failed:{exc}") from exc
        return []

    async def close_all_positions(self, symbol=None):
        """Close all open positions, optionally filtered by symbol."""
        positions = await self.get_open_positions()
        results = []
        for pos in positions or []:
            if symbol and str(pos.get("symbol") or "").upper() != str(symbol).upper():
                continue
            pos_id = str(pos.get("id") or pos.get("position_id") or "")
            if not pos_id:
                continue
            result = await self.close_position(pos_id)
            results.append(result)
        return results

    async def get_server_time(self):
        """Return broker server UTC timestamp (epoch seconds)."""
        if self._execution_adapter.available:
            fn = getattr(self._provider, "get_server_time", None)
            if callable(fn):
                try:
                    import inspect
                    result = fn()
                    if inspect.isawaitable(result):
                        result = await result
                    return float(result) if result is not None else None
                except Exception as exc:
                    if self.live:
                        raise RuntimeError(f"ctrader_get_server_time_failed:{exc}") from exc
        import time
        if self.live:
            raise RuntimeError("ctrader_live_server_time_unavailable")
        return float(time.time())

    async def get_quote(self, symbol: str):
        """Return current bid/ask quote for symbol."""
        if self._market_data_adapter is not None:
            fn = getattr(self._market_data_adapter, "get_quote", None)
            if callable(fn):
                try:
                    result = fn(symbol)
                    import inspect
                    if inspect.isawaitable(result):
                        result = await result
                    if not result:
                        return None
                    payload = dict(result)
                    import time
                    payload.setdefault("timestamp", float(time.time()))
                    payload.setdefault("quote_id", f"ctrader:{symbol}:{int(payload['timestamp'] * 1000)}")
                    return payload
                except Exception as exc:
                    if self.live:
                        raise RuntimeError(f"ctrader_get_quote_failed:{exc}") from exc
            # P0.4: In live mode, candle-derived quote is not acceptable — fail closed
            if self.live:
                raise RuntimeError("ctrader_live_quote_unavailable: broker quote required in live mode")
            # Fallback for demo/paper only: derive from candles
            try:
                candles = self._market_data_adapter.get_candles(limit=1)
                if candles is not None and not candles.empty:
                    last = candles.iloc[-1]
                    close = float(last.get("close") if hasattr(last, "get") else last["close"])
                    import time
                    ts = float(time.time())
                    return {
                        "symbol": symbol,
                        "bid": close,
                        "ask": close,
                        "spread_pips": 0.0,
                        "timestamp": ts,
                        "quote_id": f"ctrader:fallback:{symbol}:{int(ts * 1000)}",
                    }
            except Exception as exc:
                pass
        return None

