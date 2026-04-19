"""Discord webhook notification channel."""
from __future__ import annotations

import logging
import os
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)


class DiscordChannel:
    def __init__(self, webhook_url: Optional[str] = None) -> None:
        self.webhook_url = webhook_url or os.getenv("DISCORD_WEBHOOK_URL", "")

    async def send(self, notification) -> None:
        url = notification.recipient or self.webhook_url
        if not url:
            logger.warning("Discord: no webhook URL configured")
            return
        payload = {
            "embeds": [
                {
                    "title": notification.title,
                    "description": notification.body,
                    "color": 3447003,
                }
            ]
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                if resp.status not in (200, 204):
                    text = await resp.text()
                    raise RuntimeError(f"Discord webhook error {resp.status}: {text}")
        logger.info("Discord notification sent")
