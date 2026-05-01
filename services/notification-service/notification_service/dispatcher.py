"""Notification dispatcher — routes notifications to the correct channel."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Default per-channel, per-notification-type debounce window (seconds).
# During this window a second identical notification (same channel + title)
# is silently suppressed to prevent incident spikes from flooding external
# services such as Telegram or Discord.
_DEFAULT_RATE_LIMIT_SECONDS = 300  # 5 minutes


class NotificationChannel(str, Enum):
    EMAIL = "email"
    TELEGRAM = "telegram"
    DISCORD = "discord"
    WEBHOOK = "webhook"


@dataclass
class Notification:
    title: str
    body: str
    channel: NotificationChannel
    recipient: str        # email address, chat_id, webhook URL, etc.
    metadata: Optional[Dict[str, Any]] = None


class NotificationDispatcher:
    """Routes notifications to the appropriate channel handler.

    Rate limiting
    -------------
    When ``rate_limit_seconds`` is set (default 300 s / 5 min), duplicate
    notifications with the same ``(channel, title)`` key are suppressed
    within the cooldown window.  Set ``rate_limit_seconds=0`` to disable.
    """

    def __init__(self, rate_limit_seconds: float = _DEFAULT_RATE_LIMIT_SECONDS) -> None:
        self._handlers: Dict[NotificationChannel, Any] = {}
        self._rate_limit = max(0.0, float(rate_limit_seconds))
        # Maps (channel_value, title) → last_sent_ts
        self._last_sent: Dict[Tuple[str, str], float] = {}

    def register(self, channel: NotificationChannel, handler: Any) -> None:
        self._handlers[channel] = handler
        logger.info("Notification handler registered: %s", channel.value)

    def _is_rate_limited(self, notification: Notification) -> bool:
        """Return True when this notification should be suppressed."""
        if self._rate_limit <= 0:
            return False
        key = (notification.channel.value, notification.title)
        now = time.monotonic()
        last = self._last_sent.get(key, 0.0)
        if now - last < self._rate_limit:
            logger.info(
                "Notification rate-limited (%.0fs cooldown): channel=%s title=%s",
                self._rate_limit,
                notification.channel.value,
                notification.title,
            )
            return True
        self._last_sent[key] = now
        return False

    async def dispatch(self, notification: Notification) -> bool:
        if self._is_rate_limited(notification):
            return False
        handler = self._handlers.get(notification.channel)
        if handler is None:
            logger.warning("No handler for channel: %s", notification.channel.value)
            return False
        try:
            await handler.send(notification)
            logger.info(
                "Notification sent: channel=%s title=%s",
                notification.channel.value, notification.title,
            )
            return True
        except Exception as exc:
            logger.error("Notification failed: %s", exc)
            return False

    async def dispatch_multi(self, notifications: List[Notification]) -> List[bool]:
        return [await self.dispatch(n) for n in notifications]

