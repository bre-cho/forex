from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BotInstance, TradingIncident
from app.services.daily_trading_state import DailyTradingStateService
from app.services.live_readiness_guard import LiveReadinessGuard
from app.services.policy_service import PolicyService


class LiveStartPreflightError(RuntimeError):
    pass


async def run_live_start_preflight(*, bot: BotInstance, provider, db: AsyncSession) -> dict:
    checks: dict[str, bool] = {
        "broker_health": False,
        "active_policy": False,
        "daily_state_fresh": False,
        "no_daily_lock": False,
        "no_critical_incident": False,
    }

    readiness = await LiveReadinessGuard.check_provider(provider, require_live=True)
    if not readiness.ok:
        raise LiveStartPreflightError(f"provider_preflight_failed:{readiness.reason}")
    checks["broker_health"] = True

    policy = PolicyService(db)
    checks["active_policy"] = await policy.is_policy_approved_for_live(bot.id)
    if not checks["active_policy"]:
        raise LiveStartPreflightError("active_policy_missing")

    # Sync broker equity before checking daily state freshness
    daily = DailyTradingStateService(db)
    try:
        acct_info = await provider.get_account_info()
        equity = float(getattr(acct_info, "equity", None) or 0.0)
        if equity <= 0:
            raise LiveStartPreflightError("account_equity_invalid")
        state = await daily.recompute_from_broker_equity(bot.id, equity)
        await db.commit()
    except LiveStartPreflightError:
        raise
    except Exception:
        # If broker equity sync fails, fall back to existing state but mark stale
        state = await daily.get_or_create(bot.id)

    updated = state.updated_at
    now = datetime.now(timezone.utc)
    age = (now - updated).total_seconds() if updated is not None else 10**9
    checks["daily_state_fresh"] = age <= 60.0
    if not checks["daily_state_fresh"]:
        raise LiveStartPreflightError("daily_state_stale")

    # Block if daily lock is active
    if getattr(state, "locked", False):
        lock_reason = str(getattr(state, "lock_reason", None) or "daily_locked")
        raise LiveStartPreflightError(f"daily_lock_active:{lock_reason}")
    checks["no_daily_lock"] = True

    open_critical = (
        (
            await db.execute(
                select(TradingIncident).where(
                    TradingIncident.bot_instance_id == bot.id,
                    TradingIncident.status != "resolved",
                    TradingIncident.severity == "critical",
                ).limit(1)
            )
        )
        .scalar_one_or_none()
    )
    checks["no_critical_incident"] = open_critical is None
    if open_critical is not None:
        raise LiveStartPreflightError("critical_incident_open")

    return checks
