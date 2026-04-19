"""Rate limiter dependency (Redis-based sliding window)."""
from __future__ import annotations

import logging
import time

from fastapi import Depends, HTTPException, Request, status

from app.core.cache import get_redis

logger = logging.getLogger(__name__)


def rate_limit(max_requests: int = 60, window_seconds: int = 60):
    """
    Sliding-window rate limiter.
    Allows max_requests per window_seconds per IP address.
    """

    async def _limit(request: Request):
        redis = await get_redis()
        ip = request.client.host if request.client else "unknown"
        key = f"rate_limit:{ip}:{request.url.path}"
        now = int(time.time())
        window_start = now - window_seconds

        pipe = redis.pipeline()
        pipe.zremrangebyscore(key, 0, window_start)
        pipe.zadd(key, {str(now): now})
        pipe.zcard(key)
        pipe.expire(key, window_seconds)
        results = await pipe.execute()
        count = results[2]

        if count > max_requests:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded",
            )

    return _limit
