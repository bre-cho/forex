from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.credentials_crypto import decrypt_credentials
from app.models import BotInstance, BrokerConnection


class BrokerConnectionProviderFactory:
    """Build live broker providers from bot DB connection records."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_provider_for_bot(self, bot_instance_id: str):
        bot = (
            (
                await self.db.execute(
                    select(BotInstance).where(BotInstance.id == bot_instance_id).limit(1)
                )
            )
            .scalar_one_or_none()
        )
        if bot is None or not bot.broker_connection_id:
            return None

        bot_mode = str(getattr(bot, "mode", "paper") or "paper").strip().lower()
        if bot_mode != "live":
            return None

        bc = (
            (
                await self.db.execute(
                    select(BrokerConnection)
                    .where(BrokerConnection.id == bot.broker_connection_id)
                    .limit(1)
                )
            )
            .scalar_one_or_none()
        )
        if bc is None or not bool(getattr(bc, "is_active", False)):
            return None

        credentials = decrypt_credentials(str(getattr(bc, "credentials_encrypted", "") or ""))
        provider_type = str(getattr(bc, "broker_type", "") or "").lower()
        if provider_type not in {"ctrader", "mt5", "bybit"}:
            return None
        if not isinstance(credentials, dict) or not credentials:
            return None

        from trading_core.runtime import RuntimeFactory

        provider = RuntimeFactory.create_provider(
            provider_type=provider_type,
            credentials=credentials,
            symbol=str(getattr(bot, "symbol", "") or "EURUSD"),
            timeframe=str(getattr(bot, "timeframe", "") or "M5"),
            runtime_mode="live",
        )
        if str(getattr(provider, "mode", "") or "").lower() != "live":
            return None
        return provider
