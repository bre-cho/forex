"""
RuntimeFactory — creates BotRuntime instances from DB config.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


class RuntimeFactory:
    """Creates BotRuntime instances from configuration dicts."""

    @staticmethod
    def create_provider(
        provider_type: str,
        credentials: Dict[str, Any],
        symbol: str,
        timeframe: str,
    ):
        """
        Instantiate the correct broker provider based on provider_type.
        provider_type: 'ctrader' | 'paper' | 'mt5' | 'bybit'
        """
        try:
            from execution_service.providers import get_provider
        except ImportError:
            logger.warning("execution_service.providers unavailable, using local paper adapter")
            from trading_core.engines.data_provider import MockDataProvider

            class _AsyncPaperAdapter:
                def __init__(self, symbol_name: str) -> None:
                    self.symbol = symbol_name
                    self.timeframe = timeframe
                    self._provider = MockDataProvider(symbol=symbol_name)
                    self._connected = False

                @property
                def is_connected(self) -> bool:
                    return self._connected

                async def connect(self) -> None:
                    self._connected = True

                async def disconnect(self) -> None:
                    self._connected = False

                async def get_candles(self, symbol: str, timeframe: str, limit: int = 200):
                    return self._provider.get_candles(limit=limit, timeframe=timeframe)

            return _AsyncPaperAdapter(symbol)

        if provider_type == "paper":
            return get_provider("paper", symbol=symbol)
        if provider_type in {"ctrader", "mt5", "bybit"}:
            kwargs = dict(credentials or {})
            kwargs.setdefault("symbol", symbol)
            kwargs.setdefault("timeframe", timeframe)
            return get_provider(provider_type, **kwargs)
        logger.warning("Unknown provider_type '%s', falling back to paper", provider_type)
        return get_provider("paper", symbol=symbol)

    @staticmethod
    def from_bot_config(
        bot_instance_id: str,
        bot_config: Dict[str, Any],
        broker_credentials: Dict[str, Any],
    ):
        """Build a BotRuntime from the bot_instances + bot_instance_configs DB records."""
        from .bot_runtime import BotRuntime

        provider_type = bot_config.get("mode", "paper")  # 'paper' | 'live'
        actual_provider_type = "ctrader" if provider_type == "live" else "paper"

        provider = RuntimeFactory.create_provider(
            provider_type=actual_provider_type,
            credentials=broker_credentials,
            symbol=bot_config.get("symbol", "EURUSD"),
            timeframe=bot_config.get("timeframe", "M5"),
        )

        return BotRuntime(
            bot_instance_id=bot_instance_id,
            strategy_config=bot_config.get("strategy_config", {}),
            broker_provider=provider,
            risk_config=bot_config.get("risk_json", {}),
            ai_config=bot_config.get("ai_json", {}),
        )
