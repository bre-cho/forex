"""Signal broadcast — publishes signals to Redis pub/sub."""
from __future__ import annotations

import json
import logging
from typing import Optional

from .signal_builder import TradingSignal

logger = logging.getLogger(__name__)


class SignalBroadcaster:
    """
    Broadcasts TradingSignal events to a Redis pub/sub channel.
    Each bot has its own channel: signals:{bot_instance_id}
    There is also a global channel: signals:all
    """

    def __init__(self, redis_client=None) -> None:
        self._redis = redis_client

    async def broadcast(self, signal: TradingSignal) -> None:
        if self._redis is None:
            logger.debug("SignalBroadcaster: no Redis client, skipping broadcast")
            return
        payload = json.dumps(signal.to_dict())
        bot_channel = f"signals:{signal.bot_instance_id}"
        global_channel = "signals:all"
        try:
            await self._redis.publish(bot_channel, payload)
            await self._redis.publish(global_channel, payload)
            logger.debug(
                "Signal broadcast: %s → %s", signal.signal_id, bot_channel
            )
        except Exception as exc:
            logger.error("Signal broadcast failed: %s", exc)
