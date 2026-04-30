from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BotInstance, TradingIncident
from app.services.broker_capability_proof_service import BrokerCapabilityProofService
from app.services.reconciliation_daemon_health_service import ReconciliationDaemonHealthService
from app.services.submit_outbox_recovery_health_service import SubmitOutboxRecoveryHealthService
from app.services.reconciliation_queue_service import ReconciliationQueueService
from app.services.daily_trading_state import DailyTradingStateService
from app.services.live_readiness_guard import LiveReadinessGuard
from app.services.policy_service import PolicyService
from app.services.provider_certification_service import ProviderCertificationService

# Minimum required keys for a live-approved risk policy
_REQUIRED_LIVE_POLICY_KEYS = {
    "daily_take_profit",
    "max_daily_loss_pct",
    "max_margin_usage_pct",
    "max_account_exposure_pct",
    "max_symbol_exposure_pct",
    "max_correlated_usd_exposure_pct",
    "max_spread_pips",
    "max_slippage_pips",
    "min_free_margin_after_order",
    "stop_loss_required_in_live",
    "max_open_positions",
    "max_risk_amount_per_trade",
    "lock_action_on_daily_tp",
    "lock_action_on_daily_loss",
}


class LiveStartPreflightError(RuntimeError):
    pass


def _validate_policy_has_live_keys(snapshot: Any) -> None:
    """Raise if the active policy snapshot is missing any required live keys."""
    if not isinstance(snapshot, dict):
        raise LiveStartPreflightError("active_policy_snapshot_invalid")
    missing = _REQUIRED_LIVE_POLICY_KEYS - set(snapshot.keys())
    if missing:
        raise LiveStartPreflightError(f"active_policy_missing_keys:{','.join(sorted(missing))}")


async def run_live_start_preflight(*, bot: BotInstance, provider, db: AsyncSession) -> dict:
    checks: dict[str, bool] = {
        "broker_health": False,
        "broker_capability_proof": False,
        "provider_certified": False,
        "reconciliation_daemon_healthy": False,
        "submit_outbox_recovery_healthy": False,
        "active_policy": False,
        "daily_state_fresh": False,
        "no_daily_lock": False,
        "no_unknown_orders": False,
        "no_critical_incident": False,
    }

    readiness = await LiveReadinessGuard.check_provider(provider, require_live=True)
    if not readiness.ok:
        raise LiveStartPreflightError(f"provider_preflight_failed:{readiness.reason}")
    checks["broker_health"] = True

    # P0.1 — Broker capability proof: verify all live-required provider capabilities
    account_id = str(getattr(bot, "broker_account_id", "") or "")
    bot_symbol = str(getattr(bot, "symbol", "") or "EURUSD")
    bot_timeframe = str(getattr(bot, "timeframe", "") or "") or None

    provider_contract = await LiveReadinessGuard.assert_live_provider_contract(provider, symbol=bot_symbol)
    if not provider_contract.ok:
        raise LiveStartPreflightError(f"provider_contract_failed:{provider_contract.reason}")

    proof_result = await LiveReadinessGuard.require_capability_proof(
        provider,
        expected_account_id=account_id or None,
        symbol=bot_symbol,
        timeframe=bot_timeframe,
    )
    if not proof_result.ok:
        raise LiveStartPreflightError(f"provider_capability_proof_failed:{proof_result.reason}")

    proof_service = BrokerCapabilityProofService(db)
    await proof_service.record_proof(
        bot_instance_id=str(bot.id),
        provider=str(getattr(provider, "provider_name", type(provider).__name__)),
        account_id=account_id or None,
        symbol=bot_symbol,
        timeframe=bot_timeframe,
        proof_payload=dict(proof_result.details or {}),
    )
    checks["broker_capability_proof"] = True

    provider_name = str(getattr(provider, "provider_name", type(provider).__name__) or "")
    cert_svc = ProviderCertificationService(db)
    certified = await cert_svc.is_live_certified(
        bot_instance_id=str(bot.id),
        provider=provider_name,
        account_id=account_id or None,
    )
    checks["provider_certified"] = bool(certified)
    if not certified:
        raise LiveStartPreflightError("provider_not_live_certified")

    daemon_healthy = await ReconciliationDaemonHealthService.is_healthy(db)
    checks["reconciliation_daemon_healthy"] = bool(daemon_healthy)
    if not daemon_healthy:
        raise LiveStartPreflightError("reconciliation_daemon_unhealthy")

    outbox_recovery_healthy = await SubmitOutboxRecoveryHealthService.is_healthy(db)
    checks["submit_outbox_recovery_healthy"] = bool(outbox_recovery_healthy)
    if not outbox_recovery_healthy:
        raise LiveStartPreflightError("submit_outbox_recovery_unhealthy")

    policy_svc = PolicyService(db)
    is_approved = await policy_svc.is_policy_approved_for_live(bot.id)
    if not is_approved:
        raise LiveStartPreflightError("active_policy_missing")
    active_policy = await policy_svc.get_active_policy(bot.id)
    snapshot = getattr(active_policy, "policy_snapshot", None) or {}
    _validate_policy_has_live_keys(snapshot)
    checks["active_policy"] = True

    # Sync broker equity — in live mode, fail-closed: no fallback to stale state
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
    except Exception as exc:
        # Fail-closed: never start live with unverified broker equity
        raise LiveStartPreflightError(f"broker_equity_sync_failed:{type(exc).__name__}") from exc

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

    recon_queue = ReconciliationQueueService(db)
    if await recon_queue.has_unresolved(bot.id):
        raise LiveStartPreflightError("unknown_orders_unresolved")
    checks["no_unknown_orders"] = True

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
