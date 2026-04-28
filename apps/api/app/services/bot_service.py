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
    Order,
    Signal,
    Trade,
)
from app.services.daily_trading_state import DailyTradingStateService
from app.services.safety_ledger import SafetyLedgerService
from app.services.live_readiness_guard import LiveReadinessGuard

logger = logging.getLogger(__name__)

_BAD_PROVIDER_STATUSES = {"auth_failed", "disconnected", "degraded", "error"}
_STUB_PROVIDER_NAMES = {"paperprovider", "_asyncpaperadapter", "mt5provider", "bybitprovider"}


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


def _runtime_hooks(bot_id: str):
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
                    logger.warning("record_brain_cycle failed [%s]: %s", bot_id, exc)
            elif event_type == "gate_evaluated":
                try:
                    await ledger.record_gate_event(dict(payload))
                except Exception as exc:
                    logger.warning("record_gate_event failed [%s]: %s", bot_id, exc)
        await _publish_bot_event_safe(bot_id, event_type, payload)

    async def reserve_idempotency(idempotency_key: str) -> bool:
        async with AsyncSessionLocal() as db:
            ledger = SafetyLedgerService(db)
            # signal_id is not always available here; key serves as signal surrogate
            return await ledger.reserve_idempotency(bot_id, idempotency_key, idempotency_key)

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
            }


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
                state = await daily.get_or_create(bot_id)
                if float(state.starting_equity or 0.0) <= 0:
                    state.starting_equity = equity
                state.current_equity = equity
                state.daily_profit_amount = float((state.current_equity or 0.0) - (state.starting_equity or 0.0))
                if float(state.starting_equity or 0.0) > 0:
                    state.daily_loss_pct = max(0.0, -state.daily_profit_amount / float(state.starting_equity) * 100.0)
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

    return {
        "on_signal": on_signal,
        "on_order": on_order,
        "on_trade": on_trade,
        "on_trade_update": on_trade_update,
        "on_snapshot": on_snapshot,
        "on_event": on_event,
        "reserve_idempotency": reserve_idempotency,
        "get_daily_state": get_daily_state,
        "get_db_open_trades": get_db_open_trades,
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
        )
        if bot.mode == "live":
            await _assert_provider_usable(provider, bot.id)

        hooks = _runtime_hooks(bot.id)

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
            get_daily_state=hooks["get_daily_state"],
            get_db_open_trades=hooks["get_db_open_trades"],
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
    provider_name: str,
    provider_health_status: str,
    runtime_mode: str,
) -> str:
    if runtime_mode in {"stub", "not_running"} and bot_mode == "live":
        return "unavailable"
    if provider_health_status in _BAD_PROVIDER_STATUSES:
        return "degraded"
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
    provider_health_status = "unknown"
    provider_health_reason = ""

    if runtime is not None:
        provider = getattr(runtime, "broker_provider", None)
        if provider is not None:
            provider_name = provider.__class__.__name__.lower()
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
