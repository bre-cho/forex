"""cTrader execution adapter abstractions.

This module intentionally separates execution concerns from market-data concerns.
`CTraderProvider` can consume any concrete execution adapter that satisfies this
contract. In live mode, missing execution capability must fail closed.
"""
from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class ExecutionAdapterHealth:
    status: str
    reason: str = ""


class CTraderExecutionAdapter(ABC):
    """Execution-only contract for cTrader providers."""

    @property
    @abstractmethod
    def available(self) -> bool:
        """Whether this adapter can execute trades."""

    @abstractmethod
    async def connect(self) -> None:
        """Establish execution channel/session."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Close execution channel/session."""

    @abstractmethod
    async def get_account_info(self) -> Dict[str, Any]:
        """Return account info payload."""

    @abstractmethod
    async def place_market_order(
        self,
        *,
        symbol: str,
        side: str,
        volume: float,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        comment: str = "",
    ) -> Dict[str, Any]:
        """Place market order and return broker payload."""

    @abstractmethod
    async def close_position(self, *, position_id: int) -> Dict[str, Any]:
        """Close a position by broker position id."""

    @abstractmethod
    async def get_positions(self) -> List[Dict[str, Any]]:
        """List open positions."""

    @abstractmethod
    async def get_history(self, *, limit: int = 100) -> List[Dict[str, Any]]:
        """List historical trades/deals."""

    @abstractmethod
    async def health_check(self) -> ExecutionAdapterHealth:
        """Execution channel health."""


class CTraderUnavailableExecutionAdapter(CTraderExecutionAdapter):
    """Fail-closed adapter used when execution capability is unavailable."""

    def __init__(self, reason: str = "execution_adapter_unavailable") -> None:
        self._reason = reason

    @property
    def available(self) -> bool:
        return False

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def get_account_info(self) -> Dict[str, Any]:
        raise RuntimeError(self._reason)

    async def place_market_order(
        self,
        *,
        symbol: str,
        side: str,
        volume: float,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        comment: str = "",
    ) -> Dict[str, Any]:
        raise RuntimeError(self._reason)

    async def close_position(self, *, position_id: int) -> Dict[str, Any]:
        raise RuntimeError(self._reason)

    async def get_positions(self) -> List[Dict[str, Any]]:
        raise RuntimeError(self._reason)

    async def get_history(self, *, limit: int = 100) -> List[Dict[str, Any]]:
        raise RuntimeError(self._reason)

    async def health_check(self) -> ExecutionAdapterHealth:
        return ExecutionAdapterHealth(status="degraded", reason=self._reason)


class CTraderEngineExecutionAdapter(CTraderExecutionAdapter):
    """Adapter wrapping an engine object that already exposes execution methods."""

    _REQUIRED_METHODS = (
        "get_account_info",
        "place_market_order",
        "close_position",
        "get_positions",
        "get_history",
    )

    def __init__(self, engine_provider: Any) -> None:
        self._provider = engine_provider
        self._available = all(hasattr(engine_provider, name) for name in self._REQUIRED_METHODS)

    @property
    def available(self) -> bool:
        return self._available

    async def _maybe_await(self, fn, *args, **kwargs):
        value = fn(*args, **kwargs)
        if inspect.isawaitable(value):
            return await value
        return value

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def get_account_info(self) -> Dict[str, Any]:
        fn = getattr(self._provider, "get_account_info")
        return dict(await self._maybe_await(fn))

    async def place_market_order(
        self,
        *,
        symbol: str,
        side: str,
        volume: float,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        comment: str = "",
    ) -> Dict[str, Any]:
        fn = getattr(self._provider, "place_market_order")
        sig = inspect.signature(fn)
        kwargs: Dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "volume": volume,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
        }
        # Pass comment/client_order_id only if the underlying provider supports it.
        # This is critical for idempotency in live mode.
        if "comment" in sig.parameters:
            kwargs["comment"] = comment
        if "client_order_id" in sig.parameters:
            kwargs["client_order_id"] = comment
        payload = await self._maybe_await(fn, **kwargs)
        return dict(payload)

    async def close_position(self, *, position_id: int) -> Dict[str, Any]:
        fn = getattr(self._provider, "close_position")
        return dict(await self._maybe_await(fn, position_id=position_id))

    async def get_positions(self) -> List[Dict[str, Any]]:
        fn = getattr(self._provider, "get_positions")
        rows = await self._maybe_await(fn)
        return [dict(r) for r in (rows or [])]

    async def get_history(self, *, limit: int = 100) -> List[Dict[str, Any]]:
        fn = getattr(self._provider, "get_history")
        rows = await self._maybe_await(fn, limit=limit)
        return [dict(r) for r in (rows or [])]

    async def health_check(self) -> ExecutionAdapterHealth:
        if not self._available:
            return ExecutionAdapterHealth(status="degraded", reason="missing_execution_methods")
        return ExecutionAdapterHealth(status="healthy", reason="")


def build_execution_adapter(engine_provider: Any) -> CTraderExecutionAdapter:
    adapter = CTraderEngineExecutionAdapter(engine_provider)
    if adapter.available:
        return adapter
    return CTraderUnavailableExecutionAdapter("ctrader_execution_methods_missing")
