from __future__ import annotations

import asyncio

import pytest

from trading_core.runtime.runtime_registry import RuntimeRegistry
from trading_core.runtime.runtime_state import RuntimeStatus


class _FakeState:
    def __init__(self, status: RuntimeStatus) -> None:
        self.status = status


class _StopBlockingRuntime:
    def __init__(self) -> None:
        self.state = _FakeState(RuntimeStatus.RUNNING)
        self._stop_gate = asyncio.Event()

    async def stop(self) -> None:
        await self._stop_gate.wait()
        self.state.status = RuntimeStatus.STOPPED

    def release(self) -> None:
        self._stop_gate.set()


class _IdempotentRuntime:
    def __init__(self) -> None:
        self.state = _FakeState(RuntimeStatus.STOPPED)
        self.start_count = 0

    async def start(self) -> None:
        if self.state.status == RuntimeStatus.RUNNING:
            return
        self.state.status = RuntimeStatus.RUNNING
        self.start_count += 1
        await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_remove_does_not_hold_registry_lock_while_stopping():
    registry = RuntimeRegistry()
    runtime = _StopBlockingRuntime()
    registry._runtimes["bot-a"] = runtime

    remove_task = asyncio.create_task(registry.remove("bot-a"))
    await asyncio.sleep(0.01)

    created = await asyncio.wait_for(
        registry.create("bot-b", {}, broker_provider=object(), risk_config={}),
        timeout=0.3,
    )
    assert created.bot_instance_id == "bot-b"

    runtime.release()
    await remove_task


@pytest.mark.asyncio
async def test_start_is_idempotent_under_concurrent_calls():
    registry = RuntimeRegistry()
    runtime = _IdempotentRuntime()
    registry._runtimes["bot-concurrent"] = runtime  # type: ignore[assignment]

    await asyncio.gather(
        registry.start("bot-concurrent"),
        registry.start("bot-concurrent"),
    )

    assert runtime.start_count == 1
