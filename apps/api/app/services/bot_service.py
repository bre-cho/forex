"""Bot service — business logic for bot instance management."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.credentials_crypto import decrypt_credentials
from app.models import BotInstance, BotInstanceConfig, BrokerConnection

logger = logging.getLogger(__name__)


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
    if bot.broker_connection_id:
        bc_result = await db.execute(
            select(BrokerConnection).where(
                BrokerConnection.id == bot.broker_connection_id
            )
        )
        bc = bc_result.scalar_one_or_none()
        if bc:
            broker_credentials = decrypt_credentials(bc.credentials_encrypted)

    try:
        from trading_core.runtime import RuntimeFactory, RuntimeRegistry

        provider_type = "ctrader" if bot.mode == "live" else "paper"
        provider = RuntimeFactory.create_provider(
            provider_type=provider_type,
            credentials=broker_credentials,
            symbol=bot.symbol,
            timeframe=bot.timeframe,
        )
        if bot.mode == "live":
            await _assert_provider_usable(provider, bot.id)

        # Use the public registry.create() API — never access _runtimes directly
        await registry.create(
            bot_instance_id=bot.id,
            strategy_config=strategy_config,
            broker_provider=provider,
            risk_config=risk_config,
            ai_config=ai_config,
        )
        logger.info("Runtime created for bot: %s (mode=%s)", bot.id, bot.mode)

    except ImportError:
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
    if hasattr(provider, "connect") and not getattr(provider, "is_connected", False):
        await provider.connect()
    if not getattr(provider, "is_connected", False):
        raise RuntimeError(f"Live broker provider unavailable for bot {bot_id}")
    health_check = getattr(provider, "health_check", None)
    if callable(health_check):
        details = await health_check()
        if isinstance(details, dict):
            status = str(details.get("status", "healthy")).lower()
            if status in {"auth_failed", "disconnected", "degraded", "error"}:
                reason = str(details.get("reason") or "provider_not_usable")
                raise RuntimeError(f"Live broker provider unusable for bot {bot_id}: {reason}")
