"""
RuntimeFactory — creates BotRuntime instances from DB config.
"""
from __future__ import annotations

import logging
import os
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
        runtime_mode: str = "paper",
    ):
        """
        Instantiate the correct broker provider based on provider_type.
        provider_type: 'ctrader' | 'paper' | 'mt5' | 'bybit'
        """
        try:
            from execution_service.providers import get_provider
        except ImportError:
            if str(runtime_mode or "").lower() == "live" and os.environ.get("ALLOW_STUB_IN_LIVE", "false").lower() != "true":
                raise RuntimeError(
                    "Live mode requires execution_service providers. "
                    "Stub fallback is disabled when ALLOW_STUB_IN_LIVE=false."
                )
            logger.warning("execution_service.providers unavailable, using local paper adapter")
            from trading_core.engines.data_provider import MockDataProvider

            class _AsyncPaperAdapter:
                def __init__(self, symbol_name: str) -> None:
                    self.symbol = symbol_name
                    self.timeframe = timeframe
                    self.provider_name = "paper"
                    self.mode = "paper"
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

                async def health_check(self) -> Dict[str, Any]:
                    return {
                        "status": "healthy" if self._connected else "disconnected",
                        "reason": "" if self._connected else "provider_not_connected",
                    }

            return _AsyncPaperAdapter(symbol)

        if provider_type == "paper":
            return get_provider("paper", symbol=symbol)
        if provider_type in {"ctrader", "mt5", "bybit"}:
            kwargs = dict(credentials or {})
            kwargs.setdefault("symbol", symbol)
            kwargs.setdefault("timeframe", timeframe)
            if provider_type == "ctrader":
                ctrader_provider_type = "ctrader_live" if str(runtime_mode).lower() == "live" else "ctrader_demo"
                return get_provider(ctrader_provider_type, **kwargs)
            if provider_type == "mt5":
                mt5_provider_type = "mt5_live" if str(runtime_mode).lower() == "live" else "mt5_demo"
                return get_provider(mt5_provider_type, **kwargs)
            if provider_type == "bybit":
                bybit_provider_type = "bybit_live" if str(runtime_mode).lower() == "live" else "bybit_demo"
                return get_provider(bybit_provider_type, **kwargs)
            return get_provider(provider_type, **kwargs)
        raise ValueError(f"Unsupported provider_type: {provider_type!r}")

    @staticmethod
    def from_bot_config(
        bot_instance_id: str,
        bot_config: Dict[str, Any],
        broker_credentials: Dict[str, Any],
    ):
        """Build a BotRuntime from the bot_instances + bot_instance_configs DB records."""
        from .bot_runtime import BotRuntime

        runtime_mode = str(bot_config.get("mode", "paper") or "paper").lower()
        if runtime_mode == "paper":
            actual_provider_type = "paper"
        else:
            actual_provider_type = str(
                bot_config.get("broker_type") or bot_config.get("provider_type") or ""
            ).lower()
            if actual_provider_type not in {"ctrader", "mt5", "bybit"}:
                raise ValueError(f"Unsupported live provider_type: {actual_provider_type!r}")

        provider = RuntimeFactory.create_provider(
            provider_type=actual_provider_type,
            credentials=broker_credentials,
            symbol=bot_config.get("symbol", "EURUSD"),
            timeframe=bot_config.get("timeframe", "M5"),
            runtime_mode=runtime_mode,
        )

        return BotRuntime(
            bot_instance_id=bot_instance_id,
            strategy_config=bot_config.get("strategy_config", {}),
            broker_provider=provider,
            risk_config=bot_config.get("risk_json", {}),
            runtime_mode=runtime_mode,
            ai_config=bot_config.get("ai_json", {}),
        )
