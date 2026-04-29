"""Bot service — business logic for bot instance management."""
from __future__ import annotations

import logging
import os
from typing import Any
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.credentials_crypto import decrypt_credentials
from app.core.db import AsyncSessionLocal
from app.events.publishers import publish_bot_event
from app.models import (
    BotInstance,
    BotInstanceConfig,
    BotRuntimeSnapshot,
    BrokerConnection,
    DailyTradingState,
    Order,
    Signal,
    Trade,
)
from app.services.incident_notifier import notify_incident
from app.services.daily_trading_state import DailyTradingStateService
from app.services.daily_profit_lock_engine import DailyProfitLockEngine
from app.services.safety_ledger import SafetyLedgerService
from app.services.live_readiness_guard import LiveReadinessGuard
from app.services.policy_service import PolicyService
from execution_service.order_state_machine import validate_transition

logger = logging.getLogger(__name__)

_BAD_PROVIDER_STATUSES = {"auth_failed", "disconnected", "degraded", "error"}
_STUB_PROVIDER_NAMES = {"paperprovider", "_asyncpaperadapter", "_stubruntime"}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_upper(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).upper()


def _runtime_hooks(bot_id: str, bot_mode: str):
    async def _record_transition_with_validation(
        ledger: SafetyLedgerService,
        *,
        signal_id: str,
        idempotency_key: str,
        next_state: str,
        event_type: str,
        detail: str | None,
        payload: dict,
    ) -> None:
        attempt = await ledger.get_order_attempt(bot_id, idempotency_key)
        current_state = str(getattr(attempt, "current_state", "") or "INTENT_CREATED")
        decision = validate_transition(current_state, next_state)
        if not decision.ok:
            if bot_mode == "live":
                raise RuntimeError(decision.reason)
            logger.warning("invalid_order_transition [%s] %s", bot_id, decision.reason)
            return
        await ledger.record_order_state_transition(
            bot_instance_id=bot_id,
            signal_id=signal_id,
            idempotency_key=idempotency_key,
            from_state=current_state,
            to_state=str(next_state or "").upper(),
            event_type=event_type,
            detail=detail,
            payload=dict(payload),
        )
        await ledger.update_order_attempt(
            bot_instance_id=bot_id,
            idempotency_key=idempotency_key,
            status=str(getattr(attempt, "status", "PENDING_SUBMIT") if attempt is not None else "PENDING_SUBMIT"),
            current_state=str(next_state or "").upper(),
        )

    async def on_signal(payload: dict) -> None:
        async with AsyncSessionLocal() as db:
            db.add(
                Signal(
                    bot_instance_id=bot_id,
                    symbol=str(payload.get("symbol", "EURUSD")),
                    direction=_safe_upper(payload.get("direction", "HOLD"), "HOLD"),
                    confidence=_safe_float(payload.get("confidence"), 0.0),
                    wave_state=str(payload.get("wave_state", "")),
                    entry_price=_safe_float(payload.get("entry_price"), 0.0),
                    stop_loss=_safe_float(payload.get("stop_loss"), 0.0) or None,
                    take_profit=_safe_float(payload.get("take_profit"), 0.0) or None,
                    metadata_json=dict(payload),
                )
            )
            await db.commit()
        await _publish_bot_event_safe(bot_id, "signal_generated", payload)

    async def on_order(payload: dict) -> None:
        async with AsyncSessionLocal() as db:
            db.add(
                Order(
                    bot_instance_id=bot_id,
                    broker_order_id=str(payload.get("broker_order_id", "")),
                    symbol=str(payload.get("symbol", "EURUSD")),
                    side=_safe_upper(payload.get("side", "BUY"), "BUY"),
                    order_type=str(payload.get("order_type", "market")),
                    volume=_safe_float(payload.get("volume"), 0.0),
                    price=_safe_float(payload.get("price"), 0.0) or None,
                    status=str(payload.get("status", "pending")),
                )
            )
            await db.commit()
        await _publish_bot_event_safe(bot_id, "order_created", payload)

    async def on_trade(payload: dict) -> None:
        async with AsyncSessionLocal() as db:
            db.add(
                Trade(
                    bot_instance_id=bot_id,
                    broker_trade_id=str(payload.get("broker_trade_id", "")),
                    symbol=str(payload.get("symbol", "EURUSD")),
                    side=_safe_upper(payload.get("side", "BUY"), "BUY"),
                    volume=_safe_float(payload.get("volume"), 0.0),
                    entry_price=_safe_float(payload.get("entry_price"), 0.0),
                    stop_loss=_safe_float(payload.get("stop_loss"), 0.0) or None,
                    take_profit=_safe_float(payload.get("take_profit"), 0.0) or None,
                    commission=_safe_float(payload.get("commission"), 0.0),
                    status=str(payload.get("status", "open")),
                    closed_volume=_safe_float(payload.get("closed_volume"), 0.0),
                    remaining_volume=_safe_float(
                        payload.get("remaining_volume"),
                        _safe_float(payload.get("volume"), 0.0),
                    ),
                    opened_at=_now_utc(),
                )
            )
            await db.commit()
        await _publish_bot_event_safe(bot_id, "trade_opened", payload)

    async def on_trade_update(payload: dict) -> None:
        broker_trade_id = str(payload.get("broker_trade_id", ""))
        if not broker_trade_id:
            return
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Trade)
                .where(
                    Trade.bot_instance_id == bot_id,
                    Trade.broker_trade_id == broker_trade_id,
                )
                .order_by(Trade.opened_at.desc())
                .limit(1)
            )
            trade = result.scalar_one_or_none()
            if trade is None:
                return

            status = str(payload.get("status", trade.status))
            trade.status = status
            if payload.get("exit_price") is not None:
                trade.exit_price = _safe_float(payload.get("exit_price"), trade.exit_price or 0.0)
            if payload.get("pnl") is not None:
                trade.pnl = _safe_float(payload.get("pnl"), trade.pnl or 0.0)
            if payload.get("closed_volume") is not None:
                trade.closed_volume = _safe_float(payload.get("closed_volume"), trade.closed_volume)
            if payload.get("remaining_volume") is not None:
                trade.remaining_volume = _safe_float(payload.get("remaining_volume"), trade.remaining_volume)
            if status == "closed":
                trade.closed_at = _now_utc()
            await db.commit()
        await _publish_bot_event_safe(bot_id, f"trade_{status}", payload)

    async def on_snapshot(payload: dict) -> None:
        async with AsyncSessionLocal() as db:
            db.add(BotRuntimeSnapshot(bot_instance_id=bot_id, snapshot=dict(payload)))
            await db.commit()

    async def on_event(event_type: str, payload: dict) -> None:
        async with AsyncSessionLocal() as db:
            ledger = SafetyLedgerService(db)
            if event_type == "brain_cycle":
                try:
                    await ledger.record_brain_cycle(bot_id, dict(payload))
                except Exception as exc:
                    if bot_mode == "live":
                        raise RuntimeError(f"record_brain_cycle_failed:{exc}") from exc
                    logger.warning("record_brain_cycle failed [%s]: %s", bot_id, exc)
            elif event_type == "gate_evaluated":
                try:
                    await ledger.record_gate_event(dict(payload))
                except Exception as exc:
                    logger.warning("record_gate_event failed [%s]: %s", bot_id, exc)
                if str(payload.get("gate_action", "")).upper() == "ALLOW":
                    idem = str(payload.get("idempotency_key") or payload.get("signal_id") or "")
                    sig = str(payload.get("signal_id") or "")
                    try:
                        await ledger.create_or_get_order_attempt(
                            bot_instance_id=bot_id,
                            signal_id=sig,
                            brain_cycle_id=str(payload.get("brain_cycle_id") or "") or None,
                            idempotency_key=idem,
                            broker=str(payload.get("broker") or ""),
                            symbol=str(payload.get("symbol") or ""),
                            side=str(payload.get("side") or ""),
                            volume=_safe_float(payload.get("volume"), 0.0),
                            request_payload=dict(payload.get("request_payload") or payload),
                            status="PENDING_SUBMIT",
                        )
                    except Exception as exc:
                        if bot_mode == "live":
                            raise RuntimeError(f"create_order_attempt_failed:{exc}") from exc
                        logger.warning("create_order_attempt failed [%s]: %s", bot_id, exc)
                    await _record_transition_with_validation(
                        ledger,
                        signal_id=sig,
                        idempotency_key=idem,
                        next_state="GATE_ALLOWED",
                        event_type="gate_evaluated",
                        detail=str(payload.get("gate_reason") or ""),
                        payload=dict(payload),
                    )
                elif str(payload.get("gate_action", "")).upper() == "BLOCK":
                    idem = str(payload.get("idempotency_key") or payload.get("signal_id") or "")
                    sig = str(payload.get("signal_id") or "")
                    await _record_transition_with_validation(
                        ledger,
                        signal_id=sig,
                        idempotency_key=idem,
                        next_state="GATE_BLOCKED",
                        event_type="gate_evaluated",
                        detail=str(payload.get("gate_reason") or ""),
                        payload=dict(payload),
                    )
            elif event_type in {"order_filled", "order_rejected", "order_unknown"}:
                mapped_status = {
                    "order_filled": "FILLED",
                    "order_rejected": "REJECTED",
                    "order_unknown": "UNKNOWN",
                }[event_type]
                try:
                    await ledger.record_broker_order_event(dict(payload), event_type)
                    await ledger.record_execution_receipt(
                        bot_instance_id=bot_id,
                        idempotency_key=str(payload.get("idempotency_key") or payload.get("signal_id") or ""),
                        broker=str(payload.get("broker") or payload.get("provider") or "unknown"),
                        broker_order_id=str(payload.get("broker_order_id") or "") or None,
                        broker_position_id=str(payload.get("broker_position_id") or "") or None,
                        broker_deal_id=str(payload.get("broker_deal_id") or "") or None,
                        submit_status=str(payload.get("submit_status") or ("ACKED" if event_type == "order_filled" else "UNKNOWN")),
                        fill_status=str(payload.get("fill_status") or mapped_status),
                        requested_volume=_safe_float(payload.get("requested_volume"), _safe_float(payload.get("volume"), 0.0)),
                        filled_volume=_safe_float(payload.get("filled_volume"), _safe_float(payload.get("volume"), 0.0)),
                        avg_fill_price=_safe_float(payload.get("avg_fill_price"), _safe_float(payload.get("price"), 0.0)),
                        commission=_safe_float(payload.get("commission"), 0.0),
                        raw_response=dict(payload.get("raw_response") or {}),
                    )
                    await ledger.update_order_attempt(
                        bot_instance_id=bot_id,
                        idempotency_key=str(payload.get("idempotency_key") or payload.get("signal_id") or ""),
                        status=mapped_status,
                        current_state=mapped_status,
                        broker_order_id=str(payload.get("broker_order_id") or "") or None,
                        error_message=str(payload.get("error_message") or "") or None,
                    )
                    to_state = {
                        "order_filled": "ACKED",
                        "order_rejected": "REJECTED",
                        "order_unknown": "UNKNOWN",
                    }[event_type]
                    await _record_transition_with_validation(
                        ledger,
                        signal_id=str(payload.get("signal_id") or ""),
                        idempotency_key=str(payload.get("idempotency_key") or payload.get("signal_id") or ""),
                        next_state=to_state,
                        event_type=event_type,
                        detail=str(payload.get("error_message") or ""),
                        payload=dict(payload),
                    )
                    if event_type == "order_filled":
                        await _record_transition_with_validation(
                            ledger,
                            signal_id=str(payload.get("signal_id") or ""),
                            idempotency_key=str(payload.get("idempotency_key") or payload.get("signal_id") or ""),
                            next_state="FILLED",
                            event_type="order_fill_confirmed",
                            detail="receipt_fill_confirmed",
                            payload=dict(payload),
                        )
                    if event_type == "order_unknown":
                        await notify_incident(
                            incident_type="order_unknown",
                            severity="critical" if bot_mode == "live" else "warning",
                            title="Order unknown - reconciliation required",
                            detail=str(payload.get("error_message") or "broker status unknown"),
                            payload=dict(payload),
                        )
                except Exception as exc:
                    if bot_mode == "live":
                        raise RuntimeError(f"update_order_attempt_failed:{exc}") from exc
                    logger.warning("update_order_attempt failed [%s]: %s", bot_id, exc)
            elif event_type == "order_submitted":
                await _record_transition_with_validation(
                    ledger,
                    signal_id=str(payload.get("signal_id") or ""),
                    idempotency_key=str(payload.get("idempotency_key") or payload.get("signal_id") or ""),
                    next_state="SUBMITTED",
                    event_type=event_type,
                    detail="broker_submit_requested",
                    payload=dict(payload),
                )
            elif event_type == "order_reserved":
                await _record_transition_with_validation(
                    ledger,
                    signal_id=str(payload.get("signal_id") or ""),
                    idempotency_key=str(payload.get("idempotency_key") or payload.get("signal_id") or ""),
                    next_state="RESERVED",
                    event_type=event_type,
                    detail="idempotency_reserved",
                    payload=dict(payload),
                )
            elif event_type == "open_position_verified":
                await _record_transition_with_validation(
                    ledger,
                    signal_id=str(payload.get("signal_id") or ""),
                    idempotency_key=str(payload.get("idempotency_key") or payload.get("signal_id") or ""),
                    next_state="OPEN_POSITION_VERIFIED",
                    event_type=event_type,
                    detail="position_visible_in_runtime",
                    payload=dict(payload),
                )
            elif event_type == "broker_account_snapshot":
                await ledger.record_broker_account_snapshot(
                    bot_instance_id=bot_id,
                    broker=str(payload.get("broker") or "unknown"),
                    account_id=str(payload.get("account_id") or "") or None,
                    balance=_safe_float(payload.get("balance"), 0.0),
                    equity=_safe_float(payload.get("equity"), 0.0),
                    margin=_safe_float(payload.get("margin"), 0.0),
                    free_margin=_safe_float(payload.get("free_margin"), 0.0),
                    margin_level=_safe_float(payload.get("margin_level"), 0.0),
                    currency=str(payload.get("currency") or "") or None,
                    raw_response=dict(payload.get("raw_response") or payload),
                )
            elif event_type == "daily_tp_hit":
                await notify_incident(
                    incident_type="daily_tp_hit",
                    severity="warning",
                    title="Daily take-profit lock activated",
                    detail=str(payload.get("reason") or "daily_take_profit_hit"),
                    payload=dict(payload),
                )
        await _publish_bot_event_safe(bot_id, event_type, payload)

    async def reserve_idempotency(
        idempotency_key: str,
        signal_id: str | None = None,
        brain_cycle_id: str | None = None,
    ) -> bool:
        async with AsyncSessionLocal() as db:
            ledger = SafetyLedgerService(db)
            return await ledger.reserve_idempotency(
                bot_id,
                signal_id or idempotency_key,
                idempotency_key,
                brain_cycle_id,
            )

    async def verify_idempotency_reservation(
        bot_instance_id: str,
        idempotency_key: str,
        brain_cycle_id: str | None = None,
    ) -> bool:
        async with AsyncSessionLocal() as db:
            ledger = SafetyLedgerService(db)
            return await ledger.has_idempotency_reservation(
                bot_instance_id,
                idempotency_key,
                brain_cycle_id,
            )

    async def set_idempotency_status(
        idempotency_key: str,
        status: str,
        brain_cycle_id: str | None = None,
    ) -> bool:
        async with AsyncSessionLocal() as db:
            ledger = SafetyLedgerService(db)
            return await ledger.mark_idempotency_status(
                bot_id,
                idempotency_key,
                status,
                brain_cycle_id,
            )

    async def get_daily_state() -> dict | None:
        async with AsyncSessionLocal() as db:
            svc = DailyTradingStateService(db)
            state = await svc.get_or_create(bot_id)
            if state is None:
                return None
            return {
                "daily_profit_amount": float(state.daily_profit_amount or 0.0),
                "daily_loss_pct": float(state.daily_loss_pct or 0.0),
                "consecutive_losses": int(state.consecutive_losses or 0),
                "locked": bool(state.locked),
                "lock_reason": state.lock_reason or "",
                "starting_equity": float(state.starting_equity or 0.0),
                "current_equity": float(state.current_equity or 0.0),
                "updated_at": state.updated_at.isoformat() if state.updated_at else None,
            }

    async def refresh_daily_state_from_broker(equity: float | None) -> dict | None:
        async with AsyncSessionLocal() as db:
            daily = DailyTradingStateService(db)
            value = _safe_float(equity, 0.0)
            if value <= 0:
                return None
            state = await daily.recompute_from_broker_equity(bot_id, value)
            await db.commit()
            return {
                "daily_profit_amount": float(state.daily_profit_amount or 0.0),
                "daily_loss_pct": float(state.daily_loss_pct or 0.0),
                "consecutive_losses": int(state.consecutive_losses or 0),
                "locked": bool(state.locked),
                "lock_reason": state.lock_reason or "",
                "starting_equity": float(state.starting_equity or 0.0),
                "current_equity": float(state.current_equity or 0.0),
                "updated_at": state.updated_at.isoformat() if state.updated_at else None,
            }

    async def evaluate_daily_profit_lock(equity: float) -> dict | None:
        async with AsyncSessionLocal() as db:
            engine = DailyProfitLockEngine(db)
            result = await engine.evaluate_and_apply(
                bot_instance_id=bot_id,
                equity=_safe_float(equity, 0.0),
            )
            return dict(result)


    async def get_db_open_trades() -> list[dict]:
        async with AsyncSessionLocal() as db:
            rows = (
                await db.execute(
                    select(Trade).where(
                        Trade.bot_instance_id == bot_id,
                        Trade.status.in_(["open", "partial"]),
                    )
                )
            ).scalars().all()
            return [
                {
                    "id": r.id,
                    "broker_trade_id": r.broker_trade_id,
                    "symbol": r.symbol,
                    "status": r.status,
                }
                for r in rows
            ]

    async def get_policy_approval_status() -> bool:
        async with AsyncSessionLocal() as db:
            svc = PolicyService(db)
            return await svc.is_policy_approved_for_live(bot_id)

    async def get_portfolio_risk_snapshot() -> dict | None:
        async with AsyncSessionLocal() as db:
            bot_row = (
                await db.execute(select(BotInstance).where(BotInstance.id == bot_id).limit(1))
            ).scalar_one_or_none()
            if bot_row is None:
                return None

            workspace_bot_ids = (
                (
                    await db.execute(
                        select(BotInstance.id).where(BotInstance.workspace_id == bot_row.workspace_id)
                    )
                )
                .scalars()
                .all()
            )
            if not workspace_bot_ids:
                return {
                    "portfolio_daily_loss_pct": 0.0,
                    "portfolio_open_positions": 0,
                    "portfolio_kill_switch": False,
                }

            today = datetime.now(timezone.utc).date()
            states = (
                (
                    await db.execute(
                        select(DailyTradingState).where(
                            DailyTradingState.bot_instance_id.in_(workspace_bot_ids),
                            DailyTradingState.trading_day == today,
                        )
                    )
                )
                .scalars()
                .all()
            )
            portfolio_daily_loss_pct = max((float(s.daily_loss_pct or 0.0) for s in states), default=0.0)
            portfolio_kill_switch = any(bool(s.locked) for s in states)
            open_positions = (
                (
                    await db.execute(
                        select(Trade).where(
                            Trade.bot_instance_id.in_(workspace_bot_ids),
                            Trade.status.in_(["open", "partial"]),
                        )
                    )
                )
                .scalars()
                .all()
            )
            return {
                "portfolio_daily_loss_pct": float(portfolio_daily_loss_pct),
                "portfolio_open_positions": int(len(open_positions)),
                "portfolio_kill_switch": bool(portfolio_kill_switch),
            }

    async def close_db_trade(trade_id: str) -> None:
        async with AsyncSessionLocal() as db:
            row = (
                await db.execute(
                    select(Trade).where(Trade.id == trade_id, Trade.bot_instance_id == bot_id).limit(1)
                )
            ).scalar_one_or_none()
            if row is None:
                return
            row.status = "closed"
            row.closed_at = _now_utc()
            await db.commit()

    async def on_reconciliation_result(payload: dict) -> None:
        async with AsyncSessionLocal() as db:
            ledger = SafetyLedgerService(db)
            await ledger.record_reconciliation_run(payload)
            equity = float(payload.get("account_equity") or 0.0)
            if equity > 0:
                daily = DailyTradingStateService(db)
                await daily.recompute_from_broker_equity(bot_id, equity)
                await db.commit()

    async def on_reconciliation_incident(payload: dict) -> None:
        async with AsyncSessionLocal() as db:
            ledger = SafetyLedgerService(db)
            await ledger.create_incident(
                bot_instance_id=bot_id,
                incident_type=str(payload.get("incident_type") or "reconciliation_incident"),
                severity=str(payload.get("severity") or "critical"),
                title=str(payload.get("title") or "Reconciliation incident"),
                detail=str(payload.get("detail") or ""),
            )
            # Escalation: lock day so pre-execution gate blocks new orders.
            daily = DailyTradingStateService(db)
            await daily.lock_day(bot_id, "reconciliation_incident")
            await notify_incident(
                incident_type=str(payload.get("incident_type") or "reconciliation_incident"),
                severity=str(payload.get("severity") or "critical"),
                title=str(payload.get("title") or "Reconciliation incident"),
                detail=str(payload.get("detail") or ""),
                payload=dict(payload),
            )

    return {
        "on_signal": on_signal,
        "on_order": on_order,
        "on_trade": on_trade,
        "on_trade_update": on_trade_update,
        "on_snapshot": on_snapshot,
        "on_event": on_event,
        "reserve_idempotency": reserve_idempotency,
        "verify_idempotency_reservation": verify_idempotency_reservation,
        "set_idempotency_status": set_idempotency_status,
        "get_daily_state": get_daily_state,
        "refresh_daily_state_from_broker": refresh_daily_state_from_broker,
        "evaluate_daily_profit_lock": evaluate_daily_profit_lock,
        "get_portfolio_risk_snapshot": get_portfolio_risk_snapshot,
        "get_db_open_trades": get_db_open_trades,
        "get_policy_approval_status": get_policy_approval_status,
        "close_db_trade": close_db_trade,
        "on_reconciliation_result": on_reconciliation_result,
        "on_reconciliation_incident": on_reconciliation_incident,
    }


async def _publish_bot_event_safe(bot_id: str, event_type: str, payload: dict) -> None:
    try:
        await publish_bot_event(bot_id, event_type, payload)
    except Exception as exc:
        logger.warning("Bot event publish failed [%s] %s: %s", bot_id, event_type, exc)


async def create_runtime_for_bot(
    bot: BotInstance,
    registry: Any,
    db: AsyncSession,
) -> None:
    """Create and register a BotRuntime for a bot instance via registry.create().

    This is the single authoritative path for runtime creation.  It:
    1. Loads bot config (risk / strategy / ai) from DB.
    2. Loads broker credentials from DB (if a connection is attached).
    3. Builds the provider via RuntimeFactory.
    4. Registers the runtime through the public registry.create() API.
    """
    # Load per-bot config
    config_result = await db.execute(
        select(BotInstanceConfig).where(
            BotInstanceConfig.bot_instance_id == bot.id
        )
    )
    config = config_result.scalar_one_or_none()

    risk_config: dict = config.risk_json if config else {}
    strategy_config: dict = config.strategy_config if config else {}
    ai_config: dict = config.ai_json if config else {}

    # Load broker credentials (empty dict for paper mode)
    broker_credentials: dict = {}
    broker_type = "paper"
    if bot.broker_connection_id:
        bc_result = await db.execute(
            select(BrokerConnection).where(
                BrokerConnection.id == bot.broker_connection_id
            )
        )
        bc = bc_result.scalar_one_or_none()
        if bc:
            broker_credentials = decrypt_credentials(bc.credentials_encrypted)
            broker_type = bc.broker_type

    try:
        from trading_core.runtime import RuntimeFactory, RuntimeRegistry

        provider_type = "paper" if bot.mode == "paper" else broker_type
        provider = RuntimeFactory.create_provider(
            provider_type=provider_type,
            credentials=broker_credentials,
            symbol=bot.symbol,
            timeframe=bot.timeframe,
            runtime_mode=bot.mode,
        )
        if bot.mode == "live":
            await _assert_provider_usable(provider, bot.id)

        hooks = _runtime_hooks(bot.id, str(bot.mode or "paper").lower())

        # Use the public registry.create() API — never access _runtimes directly
        await registry.create(
            bot_instance_id=bot.id,
            strategy_config=strategy_config,
            broker_provider=provider,
            risk_config=risk_config,
            runtime_mode=bot.mode,
            ai_config=ai_config,
            on_signal=hooks["on_signal"],
            on_order=hooks["on_order"],
            on_trade=hooks["on_trade"],
            on_trade_update=hooks["on_trade_update"],
            on_snapshot=hooks["on_snapshot"],
            on_event=hooks["on_event"],
            reserve_idempotency=hooks["reserve_idempotency"],
            verify_idempotency_reservation=hooks["verify_idempotency_reservation"],
            set_idempotency_status=hooks["set_idempotency_status"],
            get_daily_state=hooks["get_daily_state"],
            refresh_daily_state_from_broker=hooks["refresh_daily_state_from_broker"],
            evaluate_daily_profit_lock=hooks["evaluate_daily_profit_lock"],
            get_portfolio_risk_snapshot=hooks["get_portfolio_risk_snapshot"],
            get_db_open_trades=hooks["get_db_open_trades"],
            get_policy_approval_status=hooks["get_policy_approval_status"],
            close_db_trade=hooks["close_db_trade"],
            on_reconciliation_result=hooks["on_reconciliation_result"],
            on_reconciliation_incident=hooks["on_reconciliation_incident"],
        )
        logger.info("Runtime created for bot: %s (mode=%s)", bot.id, bot.mode)

    except ImportError:
        if str(bot.mode).lower() == "live":
            raise RuntimeError(
                f"Live mode requires trading_core runtime components for bot {bot.id}"
            )
        logger.warning("trading_core not available, creating stub runtime for bot: %s", bot.id)
        await _register_stub(bot.id, registry)
    except ValueError as exc:
        # Registry raises ValueError if the runtime already exists
        logger.info("Runtime already exists for bot %s: %s", bot.id, exc)
    except Exception as exc:
        logger.error("Failed to create runtime for bot %s", bot.id)
        raise


async def _register_stub(bot_instance_id: str, registry: Any) -> None:
    """Register a minimal stub runtime when trading_core is unavailable."""

    class _StubRuntime:
        """Minimal no-op runtime for environments without trading_core."""

        def __init__(self, bot_id: str) -> None:
            self.bot_instance_id = bot_id

        async def start(self) -> None:  # noqa: D102
            logger.info("StubRuntime.start: %s", self.bot_instance_id)

        async def stop(self) -> None:  # noqa: D102
            logger.info("StubRuntime.stop: %s", self.bot_instance_id)

        async def get_snapshot(self) -> dict:  # noqa: D102
            return {"status": "stub", "bot_instance_id": self.bot_instance_id}

    stub = _StubRuntime(bot_instance_id)
    # For stub environments the registry may itself be a plain dict-like object;
    # fall back gracefully without touching private fields.
    if hasattr(registry, "create"):
        try:
            await registry.create(
                bot_instance_id=bot_instance_id,
                strategy_config={},
                broker_provider=None,
                risk_config={},
                ai_config={},
            )
        except Exception:
            pass
    else:
        logger.warning(
            "Registry does not support create(); stub runtime not registered for %s",
            bot_instance_id,
        )


async def _assert_provider_usable(provider: Any, bot_id: str) -> None:
    result = await LiveReadinessGuard.check_provider(provider, require_live=True)
    if not result.ok:
        raise RuntimeError(f"Live broker provider unusable for bot {bot_id}: {result.reason}")


def _detect_llm_mode() -> str:
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    return "stub"


def _derive_provider_mode(
    bot_mode: str,
    provider_runtime_mode: str,
    provider_name: str,
    provider_health_status: str,
    runtime_mode: str,
) -> str:
    if runtime_mode in {"stub", "not_running"} and bot_mode == "live":
        return "unavailable"
    if provider_health_status in _BAD_PROVIDER_STATUSES:
        return "degraded"
    prm = str(provider_runtime_mode or "unknown").lower()
    if bot_mode == "live" and prm in {"stub", "paper", "unavailable", "degraded", "error"}:
        return prm if prm in {"stub", "unavailable", "degraded"} else "unavailable"
    if bot_mode == "live" and prm in {"live", "demo"}:
        return "live"
    if provider_name in _STUB_PROVIDER_NAMES and bot_mode == "live":
        return "stub"
    if bot_mode == "paper":
        return "paper"
    if bot_mode == "live":
        return "live"
    return "unknown"


async def get_runtime_readiness(bot: BotInstance, registry: Any) -> dict[str, Any]:
    bot_mode = str(bot.mode or "paper").lower()
    runtime = registry.get(bot.id) if registry is not None and hasattr(registry, "get") else None

    runtime_mode = "not_running"
    runtime_error: str | None = None
    provider_name = "unknown"
    provider_runtime_mode = "unknown"
    provider_health_status = "unknown"
    provider_health_reason = ""

    if runtime is not None:
        provider = getattr(runtime, "broker_provider", None)
        if provider is not None:
            provider_name = str(
                getattr(provider, "provider_name", "")
                or provider.__class__.__name__.replace("Provider", "").lower()
            ).lower()
            provider_runtime_mode = str(getattr(provider, "mode", "unknown")).lower()
            health_check = getattr(provider, "health_check", None)
            if callable(health_check):
                try:
                    details = await health_check()
                    if isinstance(details, dict):
                        provider_health_status = str(details.get("status", "unknown")).lower()
                        provider_health_reason = str(details.get("reason") or "")
                except Exception as exc:
                    provider_health_status = "error"
                    provider_health_reason = str(exc)
            elif bool(getattr(provider, "is_connected", False)):
                provider_health_status = "healthy"
            else:
                provider_health_status = "disconnected"
                provider_health_reason = "provider_not_connected"

        snapshot_getter = getattr(runtime, "get_snapshot", None)
        if callable(snapshot_getter):
            try:
                snapshot = await snapshot_getter()
            except Exception as exc:
                snapshot = {"status": "error", "error_message": str(exc)}
            runtime_mode = str(snapshot.get("status", "unknown")).lower()
            runtime_error = snapshot.get("error_message")
            meta = snapshot.get("metadata") if isinstance(snapshot, dict) else None
            if isinstance(meta, dict):
                broker_health = meta.get("broker_health")
                if isinstance(broker_health, dict):
                    provider_health_status = str(
                        broker_health.get("status") or provider_health_status
                    ).lower()
                    provider_health_reason = str(
                        broker_health.get("reason") or provider_health_reason
                    )

    llm_mode = _detect_llm_mode()
    provider_mode = _derive_provider_mode(
        bot_mode=bot_mode,
        provider_runtime_mode=provider_runtime_mode,
        provider_name=provider_name,
        provider_health_status=provider_health_status,
        runtime_mode=runtime_mode,
    )

    hard_fail_guard_active = bot_mode == "live"
    ready_for_live_trading = (
        bot_mode == "live"
        and runtime_mode == "running"
        and provider_mode == "live"
    )

    return {
        "bot_id": bot.id,
        "bot_mode": bot_mode,
        "runtime_mode": runtime_mode,
        "runtime_error": runtime_error,
        "provider_mode": provider_mode,
        "provider_name": provider_name,
        "provider_health": {
            "status": provider_health_status,
            "reason": provider_health_reason,
        },
        "llm_mode": llm_mode,
        "hard_fail_guard_active": hard_fail_guard_active,
        "ready_for_live_trading": ready_for_live_trading,
    }


async def assert_runtime_live_guard(bot: BotInstance, registry: Any) -> None:
    if str(bot.mode).lower() != "live":
        return
    readiness = await get_runtime_readiness(bot, registry)
    runtime_mode = str(readiness.get("runtime_mode", "unknown")).lower()
    provider_mode = str(readiness.get("provider_mode", "unknown")).lower()

    runtime_bad = runtime_mode in {"stub", "error", "not_running", "degraded"}
    provider_bad = provider_mode in {"stub", "degraded", "unavailable"}
    if not runtime_bad and not provider_bad:
        return

    if registry is not None and hasattr(registry, "stop"):
        try:
            await registry.stop(bot.id)
        except Exception:
            pass

    reason = (
        readiness.get("provider_health", {}).get("reason")
        if isinstance(readiness.get("provider_health"), dict)
        else ""
    ) or readiness.get("runtime_error") or "live_guard_blocked"
    raise RuntimeError(
        f"Live runtime guard blocked bot {bot.id}: runtime_mode={runtime_mode}, "
        f"provider_mode={provider_mode}, reason={reason}"
    )
