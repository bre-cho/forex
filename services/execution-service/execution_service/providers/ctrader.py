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
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._account_id = account_id
        self.symbol = symbol
        self.timeframe = timeframe
        self.provider_name = "ctrader"
        self.live = live
        self._provider = None
        self._execution_adapter = CTraderUnavailableExecutionAdapter("execution_adapter_not_initialized")
        self._market_data_adapter = CTraderMarketDataAdapter(None)
        self._connected = False

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
