from __future__ import annotations

import asyncio
import json
import logging
import os
from urllib import request as urllib_request

logger = logging.getLogger(__name__)


async def notify_incident(
    *,
    incident_type: str,
    severity: str,
    title: str,
    detail: str,
    payload: dict | None = None,
) -> None:
    webhook_url = os.environ.get("INCIDENT_WEBHOOK_URL", "").strip()
    if not webhook_url:
        return

    body = {
        "incident_type": incident_type,
        "severity": severity,
        "title": title,
        "detail": detail,
        "payload": payload or {},
    }

    def _send() -> None:
        req = urllib_request.Request(
            webhook_url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib_request.urlopen(req, timeout=5):
            return

    try:
        await asyncio.to_thread(_send)
    except Exception as exc:
        logger.warning("incident_webhook_failed: %s", exc)
