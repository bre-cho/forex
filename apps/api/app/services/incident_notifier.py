"""Incident notifier — P1.2 Incident Alerting.

Sends critical trading incidents to configured channels:
  - INCIDENT_WEBHOOK_URL: generic webhook (legacy)
  - TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID: Telegram alert
  - DISCORD_INCIDENT_WEBHOOK_URL: Discord embed

All channels are fire-and-forget; failures are logged but never raise.
Prometheus INCIDENT_CREATED_TOTAL is incremented for every incident
regardless of channel config.

Critical incidents that always send alerts (regardless of severity filter):
  - unknown_order_escalated
  - daily_lock_close_all_postcondition_failed
  - submit_outbox_recovery_unhealthy
  - provider_disconnected_live
  - broker_account_mismatch
  - equity_drawdown_breach
  - reconciliation_sla_breach
"""
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

# Incidents to always alert regardless of severity threshold
_ALWAYS_ALERT_TYPES = {
    "unknown_order_escalated",
    "daily_lock_close_all_postcondition_failed",
    "submit_outbox_recovery_unhealthy",
    "provider_disconnected_live",
    "broker_account_mismatch",
    "equity_drawdown_breach",
    "reconciliation_sla_breach",
    "kill_switch_enabled",
}

_SEVERITY_EMOJI = {
    "critical": "🚨",
    "error": "❌",
    "warning": "⚠️",
    "info": "ℹ️",
}


def _format_alert_text(incident_type: str, severity: str, title: str, detail: str) -> str:
    emoji = _SEVERITY_EMOJI.get(str(severity).lower(), "⚠️")
    return (
        f"{emoji} *[{severity.upper()}] {title}*\n\n"
        f"Type: `{incident_type}`\n"
        f"Detail: {detail[:400]}"
    )


async def _send_telegram(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}).encode()

    def _send() -> None:
        req = urllib_request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib_request.urlopen(req, timeout=5):
            pass

    try:
        await asyncio.to_thread(_send)
    except Exception as exc:
        logger.warning("telegram_alert_failed: %s", exc)


async def _send_discord(title: str, body: str, severity: str) -> None:
    webhook_url = os.environ.get("DISCORD_INCIDENT_WEBHOOK_URL", "").strip()
    if not webhook_url:
        return
    color_map = {"critical": 15158332, "error": 15158332, "warning": 16776960, "info": 3447003}
    color = color_map.get(str(severity).lower(), 16776960)
    payload = json.dumps({
        "embeds": [{"title": f"[{severity.upper()}] {title}", "description": body[:1500], "color": color}]
    }).encode()

    def _send() -> None:
        req = urllib_request.Request(
            webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib_request.urlopen(req, timeout=5):
            pass

    try:
        await asyncio.to_thread(_send)
    except Exception as exc:
        logger.warning("discord_alert_failed: %s", exc)


async def _send_webhook(incident_type: str, severity: str, title: str, detail: str, payload: dict) -> None:
    webhook_url = os.environ.get("INCIDENT_WEBHOOK_URL", "").strip()
    if not webhook_url:
        return
    body = json.dumps({
        "incident_type": incident_type,
        "severity": severity,
        "title": title,
        "detail": detail,
        "payload": payload,
    }).encode()

    def _send() -> None:
        req = urllib_request.Request(
            webhook_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib_request.urlopen(req, timeout=5):
            pass

    try:
        await asyncio.to_thread(_send)
    except Exception as exc:
        logger.warning("incident_webhook_failed: %s", exc)


def _should_alert(incident_type: str, severity: str) -> bool:
    """Return True if this incident warrants an external alert."""
    if incident_type in _ALWAYS_ALERT_TYPES:
        return True
    # Alert on critical and error for all other incident types
    return str(severity).lower() in {"critical", "error"}


async def notify_incident(
    *,
    incident_type: str,
    severity: str,
    title: str,
    detail: str,
    payload: dict | None = None,
) -> None:
    # P1.1: always track in Prometheus
    if INCIDENT_CREATED_TOTAL is not None:
        try:
            INCIDENT_CREATED_TOTAL.labels(incident_type=incident_type, severity=severity).inc()
        except Exception:
            pass

    # P1.2: send to external channels for critical/error or always-alert types
    if _should_alert(incident_type, severity):
        alert_text = _format_alert_text(incident_type, severity, title, detail)
        await asyncio.gather(
            _send_telegram(alert_text),
            _send_discord(title, f"Type: `{incident_type}`\n" + detail, severity),
            _send_webhook(incident_type, severity, title, detail, payload or {}),
            return_exceptions=True,
        )
    else:
        # Still send to generic webhook for lower severity
        await _send_webhook(incident_type, severity, title, detail, payload or {})
