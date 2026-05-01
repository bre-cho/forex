"""signal_service.redis_consumer — Redis pub/sub consumer for TradingSignal events.

Subscribes to Redis channels published by SignalBroadcaster and delivers
TradingSignal objects to a local asyncio.Queue for consumption by BotRuntime
or any other subscriber.

Usage:
    consumer = RedisSignalConsumer(
        redis_client=redis,
        bot_instance_id="bot-123",
        on_signal=my_async_handler,
    )
    await consumer.start()
    # ... later ...
    await consumer.stop()
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Optional

from .signal_builder import TradingSignal

logger = logging.getLogger(__name__)

SignalHandler = Callable[[TradingSignal], Awaitable[None]]


class RedisSignalConsumer:
    """Subscribe to Redis pub/sub channel ``signals:{bot_instance_id}`` and
    invoke ``on_signal`` for each received TradingSignal.

    Also subscribes to the global ``signals:all`` channel so multi-bot
    broadcasts are captured when ``subscribe_global`` is True.
    """

    def __init__(
        self,
        redis_client: Any,
        bot_instance_id: str,
        on_signal: SignalHandler,
        subscribe_global: bool = False,
        max_queue: int = 1000,
    ) -> None:
        self._redis = redis_client
        self._bot_instance_id = str(bot_instance_id)
        self._on_signal = on_signal
        self._subscribe_global = subscribe_global
        self._max_queue = int(max_queue)
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    @property
    def channel(self) -> str:
        return f"signals:{self._bot_instance_id}"

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._consume_loop(),
            name=f"signal_consumer_{self._bot_instance_id}",
        )
        logger.info(
            "RedisSignalConsumer started: channel=%s global=%s",
            self.channel,
            self._subscribe_global,
        )

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("RedisSignalConsumer stopped: %s", self.channel)

    async def _consume_loop(self) -> None:
        try:
            pubsub = self._redis.pubsub()
            channels = [self.channel]
            if self._subscribe_global:
                channels.append("signals:all")
            await pubsub.subscribe(*channels)
            logger.debug("RedisSignalConsumer subscribed to %s", channels)

            while not self._stop_event.is_set():
                try:
                    message = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0),
                        timeout=2.0,
                    )
                except asyncio.TimeoutError:
                    continue
                if message is None:
                    await asyncio.sleep(0.05)
                    continue
                try:
                    raw = message.get("data")
                    if not raw:
                        continue
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    payload = json.loads(raw)
                    signal = TradingSignal.from_dict(payload)
                    await self._on_signal(signal)
                except Exception as exc:
                    logger.warning(
                        "RedisSignalConsumer: failed to process message: %s", exc
                    )
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("RedisSignalConsumer loop error: %s", exc)
        finally:
            try:
                await pubsub.unsubscribe()
                await pubsub.close()
            except Exception:
                pass
