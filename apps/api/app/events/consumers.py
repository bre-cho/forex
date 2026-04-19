"""Async event consumers — subscribe to Redis channels."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Dict

logger = logging.getLogger(__name__)


class EventConsumer:
    """Subscribes to a Redis pub/sub channel and invokes a handler per message."""

    def __init__(self, channel: str, handler: Callable[[Dict[str, Any]], None]) -> None:
        self.channel = channel
        self.handler = handler
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._consume())
        logger.info("EventConsumer started: channel=%s", self.channel)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _consume(self) -> None:
        from app.core.cache import get_redis
        redis = await get_redis()
        pubsub = redis.pubsub()
        await pubsub.subscribe(self.channel)
        async for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    data = json.loads(message["data"])
                    await self.handler(data)
                except Exception as exc:
                    logger.error("Consumer error [%s]: %s", self.channel, exc)
