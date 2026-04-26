"""Event publishers — publish events to Redis pub/sub."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict

from app.core.cache import get_redis

logger = logging.getLogger(__name__)


async def publish_event(channel: str, event_type: str, payload: Dict[str, Any]) -> None:
    """Publish an event to a Redis channel."""
    import time
    import uuid
    redis = await get_redis()
    message = json.dumps({
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "payload": payload,
        "timestamp": time.time(),
    })
    try:
        await redis.publish(channel, message)
    except Exception as exc:
        logger.error("Failed to publish event %s: %s", event_type, exc)


async def publish_bot_event(bot_id: str, event_type: str, payload: Dict[str, Any]) -> None:
    await publish_event(f"bot:{bot_id}", event_type, payload)
    # Legacy compatibility for clients still subscribed to signals:<bot_id>
    await publish_event(f"signals:{bot_id}", event_type, payload)


async def publish_workspace_event(
    workspace_id: str, event_type: str, payload: Dict[str, Any]
) -> None:
    await publish_event(f"workspace:{workspace_id}:notifications", event_type, payload)
