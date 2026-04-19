"""Generic webhook notification channel."""
from __future__ import annotations

import json
import logging
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)


class WebhookChannel:
    def __init__(self, default_url: Optional[str] = None) -> None:
        self.default_url = default_url or ""

    async def send(self, notification) -> None:
        url = notification.recipient or self.default_url
        if not url:
            logger.warning("Webhook: no URL configured")
            return
        payload = {
            "title": notification.title,
            "body": notification.body,
            "metadata": notification.metadata or {},
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
            ) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    raise RuntimeError(f"Webhook error {resp.status}: {text}")
        logger.info("Webhook notification sent to: %s", url)
