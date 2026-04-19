"""Account sync — periodically syncs broker account state to the database."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, Optional

from .providers.base import BrokerProvider

logger = logging.getLogger(__name__)


class AccountSync:
    """
    Periodically polls the broker provider for account information and
    calls the on_update callback with the latest state.
    """

    def __init__(
        self,
        provider: BrokerProvider,
        on_update: Callable[[Dict[str, Any]], None],
        interval_seconds: float = 30.0,
    ) -> None:
        self._provider = provider
        self._on_update = on_update
        self._interval = interval_seconds
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._sync_loop())
        logger.info("AccountSync started (interval=%.0fs)", self._interval)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("AccountSync stopped")

    async def _sync_loop(self) -> None:
        while True:
            try:
                info = await self._provider.get_account_info()
                self._on_update({
                    "balance": info.balance,
                    "equity": info.equity,
                    "margin": info.margin,
                    "free_margin": info.free_margin,
                    "margin_level": info.margin_level,
                    "currency": info.currency,
                })
            except Exception as exc:
                logger.error("AccountSync error: %s", exc)
            await asyncio.sleep(self._interval)
