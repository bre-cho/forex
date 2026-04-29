"""Tests for P0-D: DailyLockRuntimeController."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.daily_lock_runtime_controller import DailyLockRuntimeController


def _make_registry(**methods):
    r = MagicMock()
    for name, val in methods.items():
        setattr(r, name, AsyncMock(side_effect=val) if callable(val) else AsyncMock(return_value=val))
    return r


def _make_provider(positions=None, close_result=None):
    p = MagicMock()
    p.get_open_positions = AsyncMock(return_value=positions or [])
    p.close_position = AsyncMock(return_value=MagicMock(success=True))
    if close_result is not None:
        p.close_all_positions = AsyncMock(return_value=close_result)
    return p


# ── stop_new_orders action ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_apply_stop_new_orders():
    registry = _make_registry(pause_new_orders=None)
    ctrl = DailyLockRuntimeController(provider=_make_provider(), runtime_registry=registry)
    result = await ctrl.apply_lock_action("bot-1", "stop_new_orders")
    assert result["outcome"] == "ok"
    assert result["detail"] == "new_orders_paused"
    registry.pause_new_orders.assert_awaited_once_with("bot-1")


# ── close_all_and_stop action ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_apply_close_all_and_stop_with_close_all_positions():
    registry = _make_registry(stop=None)
    provider = _make_provider(close_result=[MagicMock(), MagicMock()])  # 2 positions closed
    ctrl = DailyLockRuntimeController(provider=provider, runtime_registry=registry)
    result = await ctrl.apply_lock_action("bot-2", "close_all_and_stop")
    assert result["outcome"] == "ok"
    assert "closed_positions:2" in result["detail"]
    registry.stop.assert_awaited_once_with("bot-2")


@pytest.mark.asyncio
async def test_apply_close_all_and_stop_fallback_per_position():
    """When provider has no close_all_positions, falls back to individual close.
    After positions are closed individually, verification call returns empty list.
    """
    registry = _make_registry(stop=None)
    provider = _make_provider(positions=[{"id": "P1"}, {"id": "P2"}])
    # no close_all_positions method
    del provider.close_all_positions
    # first call returns positions to iterate; second call (verification) returns empty
    provider.get_open_positions = AsyncMock(side_effect=[
        [{"id": "P1"}, {"id": "P2"}],
        [],
    ])
    ctrl = DailyLockRuntimeController(provider=provider, runtime_registry=registry)
    result = await ctrl.apply_lock_action("bot-3", "close_all_and_stop")
    assert result["outcome"] == "ok"
    registry.stop.assert_awaited_once()


# ── reduce_risk_only action ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_apply_reduce_risk_only():
    registry = _make_registry(set_risk_mode=None)
    ctrl = DailyLockRuntimeController(provider=_make_provider(), runtime_registry=registry)
    result = await ctrl.apply_lock_action("bot-4", "reduce_risk_only")
    assert result["outcome"] == "ok"
    assert "reduce_only" in result["detail"]
    registry.set_risk_mode.assert_awaited_once_with("bot-4", "reduce_only")


# ── unknown action ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_apply_unknown_action_returns_partial():
    ctrl = DailyLockRuntimeController(provider=_make_provider(), runtime_registry=MagicMock())
    result = await ctrl.apply_lock_action("bot-5", "fly_to_moon")
    assert result["outcome"] == "partial"


# ── on_action_completed hook ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_on_action_completed_hook_called():
    registry = _make_registry(pause_new_orders=None)
    calls = []
    async def hook(r):
        calls.append(r)
    ctrl = DailyLockRuntimeController(
        provider=_make_provider(), runtime_registry=registry, on_action_completed=hook
    )
    await ctrl.apply_lock_action("bot-6", "stop_new_orders")
    assert len(calls) == 1
    assert calls[0]["bot_id"] == "bot-6"


# ── error in registry does not crash, sets outcome=error ─────────────────────

@pytest.mark.asyncio
async def test_registry_error_sets_outcome_error():
    registry = MagicMock()
    registry.pause_new_orders = AsyncMock(side_effect=RuntimeError("registry down"))
    ctrl = DailyLockRuntimeController(provider=_make_provider(), runtime_registry=registry)
    result = await ctrl.apply_lock_action("bot-7", "stop_new_orders")
    assert result["outcome"] == "error"
    assert "registry down" in result["detail"]
