from __future__ import annotations

import pytest

from app.models import BotInstance
from app.services.bot_service import get_runtime_readiness


class _FakeProvider:
    provider_name = "mt5"
    mode = "live"
    is_connected = True

    async def health_check(self):
        return {"status": "healthy", "reason": ""}


class _FakeRuntime:
    def __init__(self) -> None:
        self.broker_provider = _FakeProvider()

    async def get_snapshot(self) -> dict:
        return {"status": "running", "metadata": {"broker_health": {"status": "healthy", "reason": ""}}}


class _FakeRegistry:
    def get(self, _bot_id: str):
        return _FakeRuntime()


@pytest.mark.asyncio
async def test_readiness_live_mt5_not_classified_stub() -> None:
    bot = BotInstance(
        id="bot-1",
        workspace_id="ws-1",
        name="Bot",
        symbol="EURUSD",
        timeframe="M5",
        mode="live",
        status="running",
    )
    readiness = await get_runtime_readiness(bot, _FakeRegistry())
    assert readiness["provider_mode"] == "live"
    assert readiness["ready_for_live_trading"] is True
