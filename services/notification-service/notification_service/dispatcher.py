"""Notification dispatcher — routes notifications to the correct channel."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


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
    """Routes notifications to the appropriate channel handler."""

    def __init__(self) -> None:
        self._handlers: Dict[NotificationChannel, Any] = {}

    def register(self, channel: NotificationChannel, handler: Any) -> None:
        self._handlers[channel] = handler
        logger.info("Notification handler registered: %s", channel.value)

    async def dispatch(self, notification: Notification) -> bool:
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
