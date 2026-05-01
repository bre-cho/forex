"""signal_service.dlq_consumer — Dead-Letter Queue consumer for signals:dlq.

Subscribes to the Redis ``signals:dlq`` channel where
:class:`RedisSignalConsumer` publishes payloads that could not be delivered
after exhausting all retry attempts.

The consumer:
1. Logs every DLQ entry as a structured error for observability.
2. Invokes an optional ``on_dlq_entry`` callback so the host (API layer) can
   persist a TradingIncident and/or fire an operator alert.
3. Maintains a per-``bot_instance_id`` entry counter so callers can monitor
   DLQ depth without polling Redis.

Usage::

    consumer = SignalDLQConsumer(
        redis_client=redis,
        on_dlq_entry=my_incident_handler,
    )
    await consumer.start()
    # ...
    await consumer.stop()
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Optional

logger = logging.getLogger(__name__)

_DLQ_CHANNEL = "signals:dlq"

DLQHandler = Callable[[Dict[str, Any]], Awaitable[None]]


@dataclass
class DLQStats:
    """Running statistics for dead-letter entries."""

    total_received: int = 0
    by_bot: Dict[str, int] = field(default_factory=dict)
    last_entry_ts: float = 0.0

    def record(self, bot_instance_id: str) -> None:
        self.total_received += 1
        self.by_bot[bot_instance_id] = self.by_bot.get(bot_instance_id, 0) + 1
        self.last_entry_ts = time.monotonic()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_received": self.total_received,
            "by_bot": dict(self.by_bot),
            "last_entry_ts": self.last_entry_ts,
        }


class SignalDLQConsumer:
    """Subscribe to ``signals:dlq`` and process dead-letter signal entries.

    Parameters
    ----------
    redis_client:
        Async Redis client (``aioredis`` / ``redis.asyncio``).
    on_dlq_entry:
        Optional async callback invoked for every DLQ entry.  Receives the
        parsed DLQ payload dict::

            {
                "original_channel": "signals:bot-123",
                "bot_instance_id":  "bot-123",
                "error":            "<exception message>",
                "payload":          "<raw JSON string>",
            }

        The callback should persist a TradingIncident and/or alert the
        operator.  Exceptions raised by the callback are caught and logged
        so they never crash the consumer loop.
    """

    def __init__(
        self,
        redis_client: Any,
        on_dlq_entry: Optional[DLQHandler] = None,
    ) -> None:
        self._redis = redis_client
        self._on_dlq_entry = on_dlq_entry
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self.stats = DLQStats()

    async def start(self) -> None:
        """Start the DLQ consumer background task."""
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._consume_loop(),
            name="signal_dlq_consumer",
        )
        logger.info("SignalDLQConsumer started — subscribing to %s", _DLQ_CHANNEL)

    async def stop(self) -> None:
        """Gracefully stop the consumer."""
        self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("SignalDLQConsumer stopped")

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _consume_loop(self) -> None:
        try:
            pubsub = self._redis.pubsub()
            await pubsub.subscribe(_DLQ_CHANNEL)
            logger.debug("SignalDLQConsumer subscribed to %s", _DLQ_CHANNEL)

            while not self._stop_event.is_set():
                try:
                    message = await asyncio.wait_for(
                        pubsub.get_message(
                            ignore_subscribe_messages=True, timeout=1.0
                        ),
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
                    await self._handle_entry(raw)
                except Exception as exc:
                    logger.warning(
                        "SignalDLQConsumer: failed to parse DLQ message: %s", exc
                    )
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("SignalDLQConsumer loop error: %s", exc)
        finally:
            try:
                await pubsub.unsubscribe()
                await pubsub.close()
            except Exception:
                pass

    async def _handle_entry(self, raw: str) -> None:
        """Parse and dispatch one DLQ entry."""
        try:
            entry: Dict[str, Any] = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error(
                "SignalDLQConsumer: non-JSON DLQ payload (len=%d): %s",
                len(raw),
                exc,
            )
            return

        bot_id = str(entry.get("bot_instance_id") or "unknown")
        error = str(entry.get("error") or "")
        channel = str(entry.get("original_channel") or _DLQ_CHANNEL)

        self.stats.record(bot_id)

        logger.error(
            "SignalDLQConsumer: DLQ entry received — bot=%s channel=%s total=%d error=%s",
            bot_id,
            channel,
            self.stats.total_received,
            error,
        )

        if self._on_dlq_entry is not None:
            try:
                await self._on_dlq_entry(entry)
            except Exception as cb_exc:
                logger.error(
                    "SignalDLQConsumer: on_dlq_entry callback failed: %s", cb_exc
                )
