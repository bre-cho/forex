"""Email notification channel via SMTP."""
from __future__ import annotations

import logging
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

logger = logging.getLogger(__name__)


class EmailChannel:
    def __init__(
        self,
        smtp_host: Optional[str] = None,
        smtp_port: int = 587,
        username: Optional[str] = None,
        password: Optional[str] = None,
        from_email: Optional[str] = None,
        use_tls: bool = True,
    ) -> None:
        self.smtp_host = smtp_host or os.getenv("SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = smtp_port or int(os.getenv("SMTP_PORT", "587"))
        self.username = username or os.getenv("SMTP_USERNAME", "")
        self.password = password or os.getenv("SMTP_PASSWORD", "")
        self.from_email = from_email or os.getenv("SMTP_FROM_EMAIL", "noreply@example.com")
        self.use_tls = use_tls

    async def send(self, notification) -> None:
        try:
            import aiosmtplib

            msg = MIMEMultipart("alternative")
            msg["Subject"] = notification.title
            msg["From"] = self.from_email
            msg["To"] = notification.recipient
            msg.attach(MIMEText(notification.body, "plain"))

            await aiosmtplib.send(
                msg,
                hostname=self.smtp_host,
                port=self.smtp_port,
                username=self.username,
                password=self.password,
                start_tls=self.use_tls,
            )
            logger.info("Email sent to: %s", notification.recipient)
        except ImportError:
            logger.error("aiosmtplib not installed; cannot send email")
        except Exception as exc:
            logger.error("Email send failed: %s", exc)
            raise
