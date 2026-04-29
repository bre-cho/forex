from __future__ import annotations

import pytest

from trading_core.runtime.runtime_registry import RuntimeRegistry


@pytest.mark.asyncio
async def test_runtime_registry_forwards_execution_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    class _FakeRuntime:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.state = type("S", (), {"status": type("V", (), {"value": "stopped"})()})()

    monkeypatch.setattr("trading_core.runtime.runtime_registry.BotRuntime", _FakeRuntime)

    async def _mark(bot_id: str, idem: str) -> None:
        return None

    async def _enqueue(bot_id: str, idem: str, signal_id: str | None, payload: dict) -> None:
        return None

    registry = RuntimeRegistry()
    await registry.create(
        bot_instance_id="bot-1",
        strategy_config={},
        broker_provider=object(),
        risk_config={},
        runtime_mode="live",
        mark_submitting_hook=_mark,
        enqueue_unknown_hook=_enqueue,
    )

    assert captured["mark_submitting_hook"] is _mark
    assert captured["enqueue_unknown_hook"] is _enqueue
