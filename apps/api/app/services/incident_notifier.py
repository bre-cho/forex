from __future__ import annotations

import asyncio
import json
import logging
import os
from urllib import request as urllib_request

logger = logging.getLogger(__name__)

try:
    from app.core.metrics import INCIDENT_CREATED_TOTAL
except Exception:
    INCIDENT_CREATED_TOTAL = None  # type: ignore[assignment]


async def notify_incident(
    *,
    incident_type: str,
    severity: str,
    title: str,
    detail: str,
    payload: dict | None = None,
) -> None:
    # P1.1: track all incidents in Prometheus regardless of webhook config
    if INCIDENT_CREATED_TOTAL is not None:
        try:
            INCIDENT_CREATED_TOTAL.labels(incident_type=incident_type, severity=severity).inc()
        except Exception:
            pass

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
