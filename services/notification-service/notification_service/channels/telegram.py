"""Telegram notification channel."""
from __future__ import annotations

import logging
import os
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramChannel:
    def __init__(
        self,
        bot_token: Optional[str] = None,
        default_chat_id: Optional[str] = None,
    ) -> None:
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.default_chat_id = default_chat_id or os.getenv("TELEGRAM_CHAT_ID", "")

    async def send(self, notification) -> None:
        chat_id = notification.recipient or self.default_chat_id
        if not self.bot_token or not chat_id:
            logger.warning("Telegram: missing bot_token or chat_id")
            return
        url = TELEGRAM_API.format(token=self.bot_token)
        payload = {
            "chat_id": chat_id,
            "text": f"*{notification.title}*\n\n{notification.body}",
            "parse_mode": "Markdown",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"Telegram API error {resp.status}: {text}")
        logger.info("Telegram message sent to: %s", chat_id)
