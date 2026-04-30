"""Submit outbox recovery worker.

Scans stale submit phases in submit_outbox and ensures UNKNOWN reconciliation
is enqueued for idempotency keys that may have been interrupted by process crash.

P0.2 checklist coverage:
- Find stale SUBMITTING/BROKER_SEND_STARTED rows.
- Auto-enqueue unknown reconciliation.
- Record append-only phase via SubmitOutboxService.
- Raise critical incidents if worker cycle fails.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import AsyncSessionLocal
from app.models import TradingIncident
from app.services.reconciliation_queue_service import ReconciliationQueueService
from app.services.submit_outbox_service import SubmitOutboxService
from app.services.worker_heartbeat_service import WorkerHeartbeatService
from app.services.incident_notifier import notify_incident

logger = logging.getLogger(__name__)

_STALE_AFTER_SECONDS = 15.0
_POLL_INTERVAL_SECONDS = 10.0


def _worker_id() -> str:
    return f"submit_outbox_recovery_{uuid.uuid4().hex[:8]}"


async def _create_worker_incident(
    db: AsyncSession,
    *,
    bot_instance_id: str,
    detail: str,
) -> None:
    dedupe_key = f"submit_outbox_recovery_unhealthy:{bot_instance_id}:{detail}"
    existing = (
        (
            await db.execute(
                select(TradingIncident)
                .where(
                    TradingIncident.bot_instance_id == bot_instance_id,
                    TradingIncident.incident_type == "submit_outbox_recovery_unhealthy",
                    TradingIncident.status == "open",
                    TradingIncident.detail.contains(dedupe_key),
                )
                .limit(1)
            )
        )
        .scalar_one_or_none()
    )
    if existing is not None:
        return

    incident = TradingIncident(
        bot_instance_id=bot_instance_id,
        incident_type="submit_outbox_recovery_unhealthy",
        severity="critical",
        title="Submit outbox recovery worker unhealthy",
        detail=f"{detail} dedupe_key={dedupe_key}",
        status="open",
    )
    db.add(incident)
    await db.commit()

    await notify_incident(
        incident_type="submit_outbox_recovery_unhealthy",
        severity="critical",
        title=incident.title,
        detail=incident.detail or "",
        payload={"bot_instance_id": bot_instance_id},
    )


async def _run_once(worker_id: str) -> None:
    async with AsyncSessionLocal() as db:
        hb = WorkerHeartbeatService(db)
        await hb.beat(
            worker_name="submit_outbox_recovery_worker",
            worker_id=worker_id,
            status="running",
            detail={"phase": "poll"},
        )

        outbox = SubmitOutboxService(db)
        stale_items = await outbox.list_stale_submit_phases(
            older_than_seconds=_STALE_AFTER_SECONDS,
            phases=("SUBMITTING", "BROKER_SEND_STARTED"),
            limit=200,
        )

    if not stale_items:
        return

    logger.warning("submit_outbox_recovery: found %d stale submit items", len(stale_items))

    for row in stale_items:
        async with AsyncSessionLocal() as db:
            queue = ReconciliationQueueService(db)
            outbox = SubmitOutboxService(db)
            payload = dict(getattr(row, "phase_payload", {}) or {})
            signal_id = str(payload.get("signal_id") or "") or None

            await queue.enqueue_unknown_order(
                bot_instance_id=str(row.bot_instance_id),
                idempotency_key=str(row.idempotency_key),
                signal_id=signal_id,
                payload={
                    **payload,
                    "reason": "stale_submit_outbox_phase",
                    "stale_phase": str(row.phase or ""),
                    "stale_updated_at": row.updated_at.isoformat() if row.updated_at else None,
                },
                auto_commit=True,
            )

            await outbox.mark_phase(
                bot_instance_id=str(row.bot_instance_id),
                idempotency_key=str(row.idempotency_key),
                phase="UNKNOWN_AFTER_SEND_RECOVERY",
                request_hash=str(row.request_hash or "") or None,
                provider=str(row.provider or "") or None,
                phase_payload={
                    **payload,
                    "reason": "stale_submit_outbox_phase",
                    "recovered_at": datetime.now(timezone.utc).isoformat(),
                },
            )


_worker_running: bool = False


async def run_submit_outbox_recovery_worker(
    *,
    poll_interval: float = _POLL_INTERVAL_SECONDS,
    stop_event: asyncio.Event | None = None,
) -> None:
    global _worker_running
    worker_id = _worker_id()
    _worker_running = True
    logger.info(
        "submit_outbox_recovery: started worker=%s interval=%.1fs",
        worker_id,
        poll_interval,
    )

    try:
        async with AsyncSessionLocal() as db:
            hb = WorkerHeartbeatService(db)
            await hb.beat(
                worker_name="submit_outbox_recovery_worker",
                worker_id=worker_id,
                status="running",
                detail={"phase": "start", "poll_interval": poll_interval},
            )
    except Exception as _hb_exc:  # non-fatal: heartbeat DB unavailable at startup
        logger.warning("submit_outbox_recovery: heartbeat start failed: %s", _hb_exc)

    try:
        while True:
            try:
                await _run_once(worker_id)
            except Exception as exc:
                logger.error("submit_outbox_recovery: unhandled cycle error: %s", exc)
                try:
                    async with AsyncSessionLocal() as db:
                        hb = WorkerHeartbeatService(db)
                        await hb.beat(
                            worker_name="submit_outbox_recovery_worker",
                            worker_id=worker_id,
                            status="error",
                            detail={"phase": "poll", "error": str(exc)},
                        )

                        outbox = SubmitOutboxService(db)
                        stale_items = await outbox.list_stale_submit_phases(
                            older_than_seconds=_STALE_AFTER_SECONDS,
                            phases=("SUBMITTING", "BROKER_SEND_STARTED"),
                            limit=50,
                        )
                        affected_bots = {str(i.bot_instance_id) for i in stale_items}
                        for bot_id in affected_bots:
                            await _create_worker_incident(
                                db,
                                bot_instance_id=bot_id,
                                detail=f"submit_outbox_recovery_cycle_error:{exc}",
                            )
                except Exception as _inner_exc:  # non-fatal: secondary DB error during error reporting
                    logger.warning("submit_outbox_recovery: error reporting failed: %s", _inner_exc)

            if stop_event is not None and stop_event.is_set():
                break

            try:
                if stop_event is not None:
                    await asyncio.wait_for(
                        asyncio.shield(asyncio.ensure_future(stop_event.wait())),
                        timeout=poll_interval,
                    )
                    break
                await asyncio.sleep(poll_interval)
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                break
    finally:
        _worker_running = False
        try:
            async with AsyncSessionLocal() as db:
                hb = WorkerHeartbeatService(db)
                await hb.beat(
                    worker_name="submit_outbox_recovery_worker",
                    worker_id=worker_id,
                    status="stopped",
                    detail={"phase": "stop"},
                )
        except Exception as _hb_stop_exc:  # non-fatal: heartbeat DB unavailable at shutdown
            logger.warning("submit_outbox_recovery: heartbeat stop failed: %s", _hb_stop_exc)

    logger.info("submit_outbox_recovery: stopped worker=%s", worker_id)
