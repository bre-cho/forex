"""Bot service — business logic for bot instance management."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BotInstance, BotInstanceConfig

logger = logging.getLogger(__name__)


async def create_runtime_for_bot(
    bot: BotInstance,
    registry: Any,
    db: AsyncSession,
) -> None:
    """Create and register a BotRuntime for a bot instance."""
    try:
        from trading_core.runtime import RuntimeFactory

        config_result = await db.execute(
            select(BotInstanceConfig).where(
                BotInstanceConfig.bot_instance_id == bot.id
            )
        )
        config = config_result.scalar_one_or_none()

        bot_config = {
            "mode": bot.mode,
            "symbol": bot.symbol,
            "timeframe": bot.timeframe,
            "risk_json": config.risk_json if config else {},
            "strategy_config": config.strategy_config if config else {},
            "ai_json": config.ai_json if config else {},
        }

        runtime = RuntimeFactory.from_bot_config(
            bot_instance_id=bot.id,
            bot_config=bot_config,
            broker_credentials={},
        )
        registry._runtimes[bot.id] = runtime
        logger.info("Runtime created for bot: %s", bot.id)
    except ImportError:
        logger.warning("trading_core not available, creating stub runtime")
        from services.stub_runtime import StubRuntime
        registry._runtimes[bot.id] = StubRuntime(bot.id)
    except Exception as exc:
        logger.error("Failed to create runtime for bot %s: %s", bot.id, exc)
        raise
