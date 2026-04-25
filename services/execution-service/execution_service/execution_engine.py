"""Execution engine — orchestrates order routing and account sync."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .account_sync import AccountSync
from .order_router import OrderRouter
from .providers.base import BrokerProvider, OrderRequest, OrderResult

logger = logging.getLogger(__name__)


class ExecutionEngine:
    """
    Top-level execution engine.

    Manages provider lifecycle, order routing, and account synchronisation
    for a single bot instance.
    """

    def __init__(
        self,
        provider: BrokerProvider,
        provider_name: str = "default",
        sync_interval: float = 30.0,
    ) -> None:
        self._provider = provider
        self._provider_name = provider_name
        self._router = OrderRouter()
        self._account_sync: Optional[AccountSync] = None
        self._last_account_info: Dict[str, Any] = {}
        self._sync_interval = sync_interval

    async def start(self) -> None:
        await self._provider.connect()
        self._router.register(self._provider_name, self._provider)
        self._account_sync = AccountSync(
            provider=self._provider,
            on_update=self._on_account_update,
            interval_seconds=self._sync_interval,
        )
        await self._account_sync.start()
        logger.info("ExecutionEngine started: provider=%s", self._provider_name)

    async def stop(self) -> None:
        if self._account_sync:
            await self._account_sync.stop()
        await self._provider.disconnect()
        logger.info("ExecutionEngine stopped")

    def _on_account_update(self, info: Dict[str, Any]) -> None:
        self._last_account_info = info

    async def place_order(self, request: OrderRequest) -> OrderResult:
        return await self._router.route(self._provider_name, request)

    async def close_position(self, position_id: str) -> OrderResult:
        return await self._router.close(self._provider_name, position_id)

    async def get_open_positions(self) -> List[Dict[str, Any]]:
        return await self._provider.get_open_positions()

    async def get_trade_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        return await self._provider.get_trade_history(limit=limit)

    @property
    def account_info(self) -> Dict[str, Any]:
        return self._last_account_info
