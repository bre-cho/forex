"""BotEventDispatcher — bridges BotRuntime on_event hook to notification channels.

Wires the ``on_event`` async callback (used by BotRuntime) to the existing
IncidentNotifier + worker-heartbeat infrastructure so that critical trading
events trigger real alerts (Telegram / Discord / webhook).

Usage (in bot lifecycle code)
------------------------------
    dispatcher = BotEventDispatcher(bot_instance_id=..., workspace_id=..., db=db_session)
    runtime.on_event = dispatcher.handle_event
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Events that should trigger an alert via IncidentNotifier
_ALERT_EVENTS = {
    "broker_disconnected",
    "broker_reconnection_failed",
    "broker_reconnected",
    "daily_tp_hit",
    "daily_loss_hit",
    "reconciliation_incident",
    "kill_switch_triggered",
}

# Events that should create a TradingIncident in the DB (severity mapping)
_INCIDENT_EVENT_SEVERITY: Dict[str, str] = {
    "broker_disconnected": "warning",
    "broker_reconnection_failed": "critical",
    "reconciliation_incident": "critical",
    "kill_switch_triggered": "critical",
    "daily_tp_hit": "info",
    "daily_loss_hit": "warning",
}


class BotEventDispatcher:
    """Translates BotRuntime events into alerts and DB incidents.

    Parameters
    ----------
    bot_instance_id:
        UUID of the bot instance whose events are being dispatched.
    workspace_id:
        UUID of the workspace owning the bot.
    db:
        Optional async SQLAlchemy session.  When provided, critical events are
        persisted as TradingIncident records.  When None, only logging + external
        alerts are used.
    """

    def __init__(
        self,
        bot_instance_id: str,
        workspace_id: str,
        db: Optional[Any] = None,
    ) -> None:
        self._bot_id = bot_instance_id
        self._workspace_id = workspace_id
        self._db = db

    async def handle_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Main handler — called by BotRuntime for every event."""
        try:
            await self._dispatch(event_type, payload)
        except Exception as exc:
            logger.error(
                "BotEventDispatcher: unhandled error dispatching event=%s bot=%s: %s",
                event_type, self._bot_id, exc,
            )

    async def _dispatch(self, event_type: str, payload: Dict[str, Any]) -> None:
        enriched = {
            "bot_instance_id": self._bot_id,
            "workspace_id": self._workspace_id,
            "event_type": event_type,
            "ts": time.time(),
            **payload,
        }

        # Always log structured event
        severity = _INCIDENT_EVENT_SEVERITY.get(event_type, "info")
        log_fn = logger.error if severity == "critical" else (logger.warning if severity == "warning" else logger.info)
        log_fn("BotEvent [%s] bot=%s: %s", event_type, self._bot_id, enriched)

        # Send alert via IncidentNotifier for high-priority events
        if event_type in _ALERT_EVENTS:
            await self._fire_alert(event_type, severity, enriched)

        # Persist critical events as TradingIncident in DB
        if severity in {"critical", "warning"} and self._db is not None:
            await self._record_incident(event_type, severity, enriched)

    async def _fire_alert(self, event_type: str, severity: str, payload: Dict[str, Any]) -> None:
        try:
            from app.services.incident_notifier import notify_incident
        except ImportError:
            return
        try:
            title = _event_to_title(event_type, payload)
            detail = _event_to_detail(event_type, payload)
            await notify_incident(
                incident_type=event_type,
                severity=severity,
                title=title,
                detail=detail,
            )
        except Exception as exc:
            logger.warning(
                "BotEventDispatcher: alert dispatch failed event=%s: %s", event_type, exc
            )

    async def _record_incident(self, event_type: str, severity: str, payload: Dict[str, Any]) -> None:
        try:
            from app.models import TradingIncident
        except ImportError:
            return
        try:
            incident = TradingIncident(
                workspace_id=self._workspace_id,
                bot_instance_id=self._bot_id,
                incident_type=event_type,
                severity=severity,
                title=_event_to_title(event_type, payload),
                detail=str(payload),
            )
            self._db.add(incident)
            await self._db.flush()
        except Exception as exc:
            logger.warning(
                "BotEventDispatcher: DB incident record failed event=%s: %s", event_type, exc
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _event_to_title(event_type: str, payload: Dict[str, Any]) -> str:
    broker = str(payload.get("broker") or "")
    bot_id = str(payload.get("bot_instance_id") or "")
    titles = {
        "broker_disconnected": f"Broker disconnected [{broker}] bot={bot_id}",
        "broker_reconnection_failed": f"Broker reconnection FAILED [{broker}] bot={bot_id}",
        "broker_reconnected": f"Broker reconnected [{broker}] bot={bot_id}",
        "daily_tp_hit": f"Daily take-profit hit bot={bot_id}",
        "daily_loss_hit": f"Daily loss limit hit bot={bot_id}",
        "reconciliation_incident": f"Reconciliation incident bot={bot_id}",
        "kill_switch_triggered": f"KILL SWITCH triggered bot={bot_id}",
    }
    return titles.get(event_type, f"BotEvent: {event_type} bot={bot_id}")


def _event_to_detail(event_type: str, payload: Dict[str, Any]) -> str:
    parts = []
    for key in ("reason", "lock_action", "target", "broker", "symbol", "pnl", "ts"):
        if key in payload:
            parts.append(f"{key}={payload[key]}")
    return ", ".join(parts) if parts else str(payload)
