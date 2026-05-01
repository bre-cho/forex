"""signal_service.redis_consumer — Redis pub/sub consumer for TradingSignal events.

Subscribes to Redis channels published by SignalBroadcaster and delivers
TradingSignal objects to a local asyncio.Queue for consumption by BotRuntime
or any other subscriber.

Failed signals are retried up to ``max_retries`` times with a short delay.
After exhausting retries the raw payload is published to the dead-letter
channel ``signals:dlq`` for operator inspection and replay.

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

_DLQ_CHANNEL = "signals:dlq"
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_RETRY_DELAY = 0.5  # seconds


class RedisSignalConsumer:
    """Subscribe to Redis pub/sub channel ``signals:{bot_instance_id}`` and
    invoke ``on_signal`` for each received TradingSignal.

    Also subscribes to the global ``signals:all`` channel so multi-bot
    broadcasts are captured when ``subscribe_global`` is True.

    Failed deliveries are retried up to ``max_retries`` times.  Signals that
    cannot be delivered after all retries are published to ``signals:dlq``.
    """

    def __init__(
        self,
        redis_client: Any,
        bot_instance_id: str,
        on_signal: SignalHandler,
        subscribe_global: bool = False,
        max_queue: int = 1000,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        retry_delay: float = _DEFAULT_RETRY_DELAY,
    ) -> None:
        self._redis = redis_client
        self._bot_instance_id = str(bot_instance_id)
        self._on_signal = on_signal
        self._subscribe_global = subscribe_global
        self._max_queue = int(max_queue)
        self._max_retries = max(0, int(max_retries))
        self._retry_delay = max(0.0, float(retry_delay))
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
            "RedisSignalConsumer started: channel=%s global=%s max_retries=%d",
            self.channel,
            self._subscribe_global,
            self._max_retries,
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

    async def _deliver_with_retry(self, signal: TradingSignal, raw: str) -> None:
        """Invoke on_signal with retry logic and DLQ fallback."""
        last_exc: Optional[Exception] = None
        for attempt in range(self._max_retries + 1):
            try:
                await self._on_signal(signal)
                return
            except Exception as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    logger.warning(
                        "RedisSignalConsumer: signal delivery attempt %d/%d failed "
                        "(channel=%s): %s — retrying in %.1fs",
                        attempt + 1,
                        self._max_retries + 1,
                        self.channel,
                        exc,
                        self._retry_delay,
                    )
                    await asyncio.sleep(self._retry_delay)

        # All retries exhausted — publish to dead-letter queue.
        logger.error(
            "RedisSignalConsumer: signal delivery failed after %d attempts "
            "(channel=%s): %s — publishing to DLQ",
            self._max_retries + 1,
            self.channel,
            last_exc,
        )
        await self._publish_to_dlq(raw, error=str(last_exc))

    async def _publish_to_dlq(self, raw: str, *, error: str) -> None:
        """Publish a failed signal payload to the dead-letter channel."""
        try:
            dlq_payload = json.dumps({
                "original_channel": self.channel,
                "bot_instance_id": self._bot_instance_id,
                "error": error,
                "payload": raw,
            })
            await self._redis.publish(_DLQ_CHANNEL, dlq_payload)
            logger.info(
                "RedisSignalConsumer: published dead-letter entry to %s", _DLQ_CHANNEL
            )
        except Exception as dlq_exc:
            logger.error(
                "RedisSignalConsumer: DLQ publish failed: %s", dlq_exc
            )

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
                    await self._deliver_with_retry(signal, raw)
                except Exception as exc:
                    logger.warning(
                        "RedisSignalConsumer: failed to parse message: %s", exc
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

