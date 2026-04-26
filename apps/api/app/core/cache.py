"""Redis cache client wrapper."""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

try:
    import redis.asyncio as aioredis
except ImportError:  # pragma: no cover - fallback for minimal test environments
    aioredis = None

from .config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_redis_client: Optional[Any] = None


async def get_redis() -> Any:
    global _redis_client
    if _redis_client is None:
        if aioredis is None:
            raise RuntimeError("redis package is not installed")
        _redis_client = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis_client


async def cache_get(key: str) -> Optional[Any]:
    redis = await get_redis()
    value = await redis.get(key)
    if value is None:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


async def cache_set(key: str, value: Any, ttl: int = None) -> None:
    redis = await get_redis()
    ttl = ttl or settings.redis_cache_ttl
    serialized = json.dumps(value) if not isinstance(value, str) else value
    await redis.setex(key, ttl, serialized)


async def cache_delete(key: str) -> None:
    redis = await get_redis()
    await redis.delete(key)


async def publish(channel: str, message: Any) -> None:
    redis = await get_redis()
    payload = json.dumps(message) if not isinstance(message, str) else message
    await redis.publish(channel, payload)


async def close_redis() -> None:
    global _redis_client
    if _redis_client:
        await _redis_client.aclose()
        _redis_client = None
