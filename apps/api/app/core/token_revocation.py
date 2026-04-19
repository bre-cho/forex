"""Helpers for JWT revocation checks backed by Redis."""
from __future__ import annotations

import time
from datetime import datetime

from app.core.cache import get_redis

_USER_ACCESS_REVOKED_AFTER_PREFIX = "auth:revoke_after:access:user:"


def normalize_iat_ms(token_iat: int | float | str | datetime | None) -> int | None:
    if token_iat is None:
        return None
    if isinstance(token_iat, datetime):
        return int(token_iat.timestamp() * 1000)
    try:
        value = float(token_iat)
        # Values above ~1e12 are treated as unix timestamps in milliseconds.
        if value > 1_000_000_000_000:
            return int(value)
        return int(value * 1000)
    except (TypeError, ValueError):
        return None


async def revoke_all_user_access_tokens(user_id: str) -> None:
    redis = await get_redis()
    await redis.set(f"{_USER_ACCESS_REVOKED_AFTER_PREFIX}{user_id}", int(time.time() * 1000))


async def is_user_access_token_revoked_after(user_id: str, token_iat: int | None) -> bool:
    if token_iat is None:
        return False
    redis = await get_redis()
    value = await redis.get(f"{_USER_ACCESS_REVOKED_AFTER_PREFIX}{user_id}")
    if value is None:
        return False
    try:
        return int(token_iat) <= int(value)
    except (TypeError, ValueError):
        return False
