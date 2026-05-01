"""Tests for P0.4 — BotRuntime.start() enforces risk state hooks for live mode."""
from __future__ import annotations

import asyncio
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trading_core.runtime.bot_runtime import BotRuntime
from trading_core.runtime.runtime_state import RuntimeStatus


def _make_paper_provider():
    provider = MagicMock()
    provider.mode = "paper"
    provider.is_connected = True
    provider.provider_name = "paper"
    provider.connect = AsyncMock()
    provider.disconnect = AsyncMock()
    provider.health_check = AsyncMock(return_value={"status": "healthy"})
    provider.get_account_info = AsyncMock(return_value=MagicMock(equity=10000.0, balance=10000.0))
    provider.get_open_positions = AsyncMock(return_value=[])
    return provider


def _noop_load(bid: str) -> None:
    async def _inner(bid: str):
        return None
    return _inner(bid)


class TestBotRuntimeLiveHookEnforcement:
    """P0.4 — live mode must fail-closed when risk state hooks are missing."""

    def _make_runtime(self, *, load_hook=None, save_hook=None, mode: str = "live"):
        provider = _make_paper_provider()
        return BotRuntime(
            bot_instance_id="test-bot",
            strategy_config={},
            broker_provider=provider,
            risk_config={},
            runtime_mode=mode,
            load_risk_state=load_hook,
            save_risk_state=save_hook,
        )

    @pytest.mark.asyncio
    async def test_live_mode_raises_when_hooks_missing(self):
        runtime = self._make_runtime(load_hook=None, save_hook=None, mode="live")
        with pytest.raises(RuntimeError, match="load_risk_state"):
            await runtime.start()

    @pytest.mark.asyncio
    async def test_live_mode_raises_when_only_load_missing(self):
        async def save(bid, payload): pass
        runtime = self._make_runtime(load_hook=None, save_hook=save, mode="live")
        with pytest.raises(RuntimeError, match="load_risk_state"):
            await runtime.start()

    @pytest.mark.asyncio
    async def test_live_mode_raises_when_only_save_missing(self):
        async def load(bid): return None
        runtime = self._make_runtime(load_hook=load, save_hook=None, mode="live")
        with pytest.raises(RuntimeError, match="save_risk_state"):
            await runtime.start()

    @pytest.mark.asyncio
    async def test_paper_mode_starts_without_hooks(self, monkeypatch):
        """Paper mode should NOT require risk state hooks."""
        runtime = self._make_runtime(load_hook=None, save_hook=None, mode="paper")

        # Patch away engine init and execution so we don't need full environment
        monkeypatch.setattr(runtime, "_init_engines", lambda: None)
        monkeypatch.setattr(runtime, "_load_risk_state_from_store", AsyncMock())
        monkeypatch.setattr(runtime, "_ensure_provider_usable", AsyncMock())
        monkeypatch.setattr(runtime, "_run_loop", AsyncMock())

        # Should not raise
        await runtime.start()
        assert runtime.state.status == RuntimeStatus.RUNNING
        await runtime.stop()

    @pytest.mark.asyncio
    async def test_live_mode_starts_successfully_with_hooks(self, monkeypatch):
        async def load(bid): return None
        async def save(bid, payload): pass

        runtime = self._make_runtime(load_hook=load, save_hook=save, mode="live")

        monkeypatch.setattr(runtime, "_init_engines", lambda: None)
        monkeypatch.setattr(runtime, "_load_risk_state_from_store", AsyncMock())
        monkeypatch.setattr(runtime, "_ensure_provider_usable", AsyncMock())
        monkeypatch.setattr(runtime, "_start_reconciliation_worker", AsyncMock())
        monkeypatch.setattr(runtime, "_broker_heartbeat_loop", AsyncMock())
        monkeypatch.setattr(runtime, "_account_sync_loop", AsyncMock())
        monkeypatch.setattr(runtime, "_run_loop", AsyncMock())

        await runtime.start()
        assert runtime.state.status == RuntimeStatus.RUNNING
        await runtime.stop()
