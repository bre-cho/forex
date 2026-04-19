"""
RuntimeRegistry — manages all active BotRuntime instances.

This is the central in-process registry for the trading platform.
It replaces the global AppState singleton, enabling true multi-bot operation.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, Optional

from .bot_runtime import BotRuntime
from .runtime_state import RuntimeStatus

logger = logging.getLogger(__name__)


class RuntimeRegistry:
    """
    Thread-safe registry of active BotRuntime instances.

    Usage:
        registry = RuntimeRegistry()
        runtime = await registry.create(bot_instance_id, config)
        await registry.start(bot_instance_id)
        snapshot = await registry.get_snapshot(bot_instance_id)
        await registry.stop(bot_instance_id)
    """

    def __init__(self) -> None:
        self._runtimes: Dict[str, BotRuntime] = {}
        self._lock = asyncio.Lock()
        logger.info("RuntimeRegistry initialised")

    async def create(
        self,
        bot_instance_id: str,
        strategy_config: dict,
        broker_provider: object,
        risk_config: dict,
        ai_config: Optional[dict] = None,
    ) -> BotRuntime:
        """Register a new BotRuntime.  Raises ValueError if one already exists."""
        async with self._lock:
            if bot_instance_id in self._runtimes:
                raise ValueError(f"Runtime already exists: {bot_instance_id}")
            runtime = BotRuntime(
                bot_instance_id=bot_instance_id,
                strategy_config=strategy_config,
                broker_provider=broker_provider,
                risk_config=risk_config,
                ai_config=ai_config,
            )
            self._runtimes[bot_instance_id] = runtime
        logger.info(
            "Runtime registered: %s (total: %d)", bot_instance_id, len(self._runtimes)
        )
        return runtime

    def get(self, bot_instance_id: str) -> Optional[BotRuntime]:
        return self._runtimes.get(bot_instance_id)

    def get_or_raise(self, bot_instance_id: str) -> BotRuntime:
        runtime = self.get(bot_instance_id)
        if runtime is None:
            raise KeyError(f"Runtime not found: {bot_instance_id}")
        return runtime

    async def start(self, bot_instance_id: str) -> None:
        # Retrieve the runtime under the lock (protecting _runtimes dict),
        # then release before awaiting the async operation to avoid holding
        # the lock during potentially long I/O and to prevent deadlocks.
        async with self._lock:
            runtime = self.get_or_raise(bot_instance_id)
        await runtime.start()

    async def stop(self, bot_instance_id: str) -> None:
        async with self._lock:
            runtime = self.get_or_raise(bot_instance_id)
        await runtime.stop()

    async def pause(self, bot_instance_id: str) -> None:
        async with self._lock:
            runtime = self.get_or_raise(bot_instance_id)
        await runtime.pause()

    async def resume(self, bot_instance_id: str) -> None:
        async with self._lock:
            runtime = self.get_or_raise(bot_instance_id)
        await runtime.resume()

    async def remove(self, bot_instance_id: str) -> None:
        async with self._lock:
            runtime = self._runtimes.get(bot_instance_id)
            if runtime is None:
                return
            if runtime.state.status == RuntimeStatus.RUNNING:
                await runtime.stop()
            del self._runtimes[bot_instance_id]
        logger.info(
            "Runtime removed: %s (remaining: %d)", bot_instance_id, len(self._runtimes)
        )

    async def get_snapshot(self, bot_instance_id: str) -> dict:
        return await self.get_or_raise(bot_instance_id).get_snapshot()

    def list_all(self) -> list[dict]:
        return [
            {
                "bot_instance_id": bid,
                "status": rt.state.status.value,
                "started_at": rt.state.started_at,
            }
            for bid, rt in self._runtimes.items()
        ]

    async def stop_all(self) -> None:
        async with self._lock:
            targets = [
                rt for rt in self._runtimes.values()
                if rt.state.status == RuntimeStatus.RUNNING
            ]
        for runtime in targets:
            await runtime.stop()
        logger.info("All runtimes stopped")
