"""Tests for P0-A: Live start preflight fail-closed."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.live_start_preflight import (
    run_live_start_preflight,
    LiveStartPreflightError,
    _validate_policy_has_live_keys,
    _REQUIRED_LIVE_POLICY_KEYS,
)


def _make_valid_policy_snapshot():
    return {
        "daily_take_profit": {"enabled": True, "mode": "fixed_amount", "amount": 20},
        "max_daily_loss_pct": 5.0,
        "max_margin_usage_pct": 80.0,
        "max_account_exposure_pct": 50.0,
    }


# ── _validate_policy_has_live_keys ────────────────────────────────────────────

def test_validate_policy_passes_complete_snapshot():
    _validate_policy_has_live_keys(_make_valid_policy_snapshot())  # must not raise


def test_validate_policy_raises_on_missing_keys():
    snapshot = {"max_daily_loss_pct": 5.0}  # missing 3 required keys
    with pytest.raises(LiveStartPreflightError, match="active_policy_missing_keys"):
        _validate_policy_has_live_keys(snapshot)


def test_validate_policy_raises_on_non_dict():
    with pytest.raises(LiveStartPreflightError, match="active_policy_snapshot_invalid"):
        _validate_policy_has_live_keys(None)
    with pytest.raises(LiveStartPreflightError, match="active_policy_snapshot_invalid"):
        _validate_policy_has_live_keys("bad")


# ── broker equity sync fail-closed ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_preflight_fails_closed_if_equity_sync_raises():
    """If broker.get_account_info() raises, preflight must fail with broker_equity_sync_failed."""
    bot = MagicMock()
    bot.id = "bot-1"

    provider = MagicMock()
    provider.get_account_info = AsyncMock(side_effect=ConnectionError("broker timeout"))

    db = AsyncMock(spec=AsyncSession)

    readiness_ok = MagicMock()
    readiness_ok.ok = True

    policy_mock = MagicMock()
    policy_mock.is_policy_approved_for_live = AsyncMock(return_value=True)
    active_policy = MagicMock()
    active_policy.policy_snapshot = _make_valid_policy_snapshot()
    active_policy.status = "active"
    policy_mock.get_active_policy = AsyncMock(return_value=active_policy)

    with patch("app.services.live_start_preflight.LiveReadinessGuard.check_provider", AsyncMock(return_value=readiness_ok)), \
         patch("app.services.live_start_preflight.PolicyService", return_value=policy_mock):
        with pytest.raises(LiveStartPreflightError, match="broker_equity_sync_failed"):
            await run_live_start_preflight(bot=bot, provider=provider, db=db)


@pytest.mark.asyncio
async def test_preflight_fails_if_equity_is_zero():
    bot = MagicMock()
    bot.id = "bot-2"

    acct = MagicMock()
    acct.equity = 0.0
    provider = MagicMock()
    provider.get_account_info = AsyncMock(return_value=acct)

    db = AsyncMock(spec=AsyncSession)
    readiness_ok = MagicMock()
    readiness_ok.ok = True

    policy_mock = MagicMock()
    policy_mock.is_policy_approved_for_live = AsyncMock(return_value=True)
    active_policy = MagicMock()
    active_policy.policy_snapshot = _make_valid_policy_snapshot()
    policy_mock.get_active_policy = AsyncMock(return_value=active_policy)

    with patch("app.services.live_start_preflight.LiveReadinessGuard.check_provider", AsyncMock(return_value=readiness_ok)), \
         patch("app.services.live_start_preflight.PolicyService", return_value=policy_mock):
        with pytest.raises(LiveStartPreflightError, match="account_equity_invalid"):
            await run_live_start_preflight(bot=bot, provider=provider, db=db)


@pytest.mark.asyncio
async def test_preflight_blocks_when_unknown_orders_unresolved():
    bot = MagicMock()
    bot.id = "bot-3"

    acct = MagicMock()
    acct.equity = 1000.0
    provider = MagicMock()
    provider.get_account_info = AsyncMock(return_value=acct)

    db = AsyncMock(spec=AsyncSession)
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))

    readiness_ok = MagicMock()
    readiness_ok.ok = True

    policy_mock = MagicMock()
    policy_mock.is_policy_approved_for_live = AsyncMock(return_value=True)
    active_policy = MagicMock()
    active_policy.policy_snapshot = _make_valid_policy_snapshot()
    policy_mock.get_active_policy = AsyncMock(return_value=active_policy)

    daily_state = MagicMock()
    daily_state.updated_at = datetime.now(timezone.utc)
    daily_state.locked = False
    daily_state.lock_reason = None
    daily_svc = MagicMock()
    daily_svc.recompute_from_broker_equity = AsyncMock(return_value=daily_state)

    queue_svc = MagicMock()
    queue_svc.has_unresolved = AsyncMock(return_value=True)

    with patch("app.services.live_start_preflight.LiveReadinessGuard.check_provider", AsyncMock(return_value=readiness_ok)), \
         patch("app.services.live_start_preflight.PolicyService", return_value=policy_mock), \
         patch("app.services.live_start_preflight.DailyTradingStateService", return_value=daily_svc), \
            patch("app.services.live_start_preflight.ReconciliationQueueService", return_value=queue_svc):
        with pytest.raises(LiveStartPreflightError, match="unknown_orders_unresolved"):
            await run_live_start_preflight(bot=bot, provider=provider, db=db)
