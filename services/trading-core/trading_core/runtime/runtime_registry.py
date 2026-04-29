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
        self._op_locks: Dict[str, asyncio.Lock] = {}
        logger.info("RuntimeRegistry initialised")

    def _get_or_create_op_lock(self, bot_instance_id: str) -> asyncio.Lock:
        lock = self._op_locks.get(bot_instance_id)
        if lock is None:
            lock = asyncio.Lock()
            self._op_locks[bot_instance_id] = lock
        return lock

    async def create(
        self,
        bot_instance_id: str,
        strategy_config: dict,
        broker_provider: object,
        risk_config: dict,
        runtime_mode: str = "paper",
        ai_config: Optional[dict] = None,
        on_signal=None,
        on_order=None,
        on_trade=None,
        on_trade_update=None,
        on_snapshot=None,
        on_event=None,
        reserve_idempotency=None,
        verify_idempotency_reservation=None,
        set_idempotency_status=None,
        get_daily_state=None,
        refresh_daily_state_from_broker=None,
        get_db_open_trades=None,
        close_db_trade=None,
        on_reconciliation_result=None,
        on_reconciliation_incident=None,
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
                runtime_mode=runtime_mode,
                ai_config=ai_config,
                on_signal=on_signal,
                on_order=on_order,
                on_trade=on_trade,
                on_trade_update=on_trade_update,
                on_snapshot=on_snapshot,
                on_event=on_event,
                reserve_idempotency=reserve_idempotency,
                verify_idempotency_reservation=verify_idempotency_reservation,
                set_idempotency_status=set_idempotency_status,
                get_daily_state=get_daily_state,
                refresh_daily_state_from_broker=refresh_daily_state_from_broker,
                get_db_open_trades=get_db_open_trades,
                close_db_trade=close_db_trade,
                on_reconciliation_result=on_reconciliation_result,
                on_reconciliation_incident=on_reconciliation_incident,
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
        async with self._lock:
            runtime = self.get_or_raise(bot_instance_id)
            op_lock = self._get_or_create_op_lock(bot_instance_id)
        async with op_lock:
            await runtime.start()

    async def stop(self, bot_instance_id: str) -> None:
        async with self._lock:
            runtime = self.get_or_raise(bot_instance_id)
            op_lock = self._get_or_create_op_lock(bot_instance_id)
        async with op_lock:
            await runtime.stop()

    async def pause(self, bot_instance_id: str) -> None:
        async with self._lock:
            runtime = self.get_or_raise(bot_instance_id)
            op_lock = self._get_or_create_op_lock(bot_instance_id)
        async with op_lock:
            await runtime.pause()

    async def resume(self, bot_instance_id: str) -> None:
        async with self._lock:
            runtime = self.get_or_raise(bot_instance_id)
            op_lock = self._get_or_create_op_lock(bot_instance_id)
        async with op_lock:
            await runtime.resume()

    async def remove(self, bot_instance_id: str) -> None:
        async with self._lock:
            runtime = self._runtimes.pop(bot_instance_id, None)
            if runtime is None:
                return
            op_lock = self._get_or_create_op_lock(bot_instance_id)
            remaining = len(self._runtimes)
        try:
            async with op_lock:
                if runtime.state.status != RuntimeStatus.STOPPED:
                    await runtime.stop()
        except Exception:
            async with self._lock:
                self._runtimes[bot_instance_id] = runtime
            raise
        finally:
            async with self._lock:
                self._op_locks.pop(bot_instance_id, None)
        logger.info(
            "Runtime removed: %s (remaining: %d)", bot_instance_id, remaining
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
                (bid, rt) for bid, rt in self._runtimes.items()
                if rt.state.status == RuntimeStatus.RUNNING
            ]
            op_locks = {bid: self._get_or_create_op_lock(bid) for bid, _ in targets}
        for bot_instance_id, runtime in targets:
            async with op_locks[bot_instance_id]:
                await runtime.stop()
        logger.info("All runtimes stopped")
