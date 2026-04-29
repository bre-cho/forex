"""Reconciliation Daemon — Unknown Order background worker.

Runs as an asyncio task independent of any BotRuntime. Polls the
reconciliation_queue_items table and applies time-based escalation policy:

  • item age > 30 s → retry (if attempts < max_attempts)
  • item past deadline_at OR attempts >= max_attempts → dead-letter
    + create TradingIncident(severity="critical")
    + lock the affected bot (set daily_trading_states.locked=True)

Audit spec: P0.5 — Unknown Order Daemon
  "Unknown order > 30s: retry"
  "Unknown order > 5 min OR 3 fail: daily lock + critical incident"
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import AsyncSessionLocal
from app.models import ReconciliationQueueItem, TradingIncident
from app.services.reconciliation_lease_service import ReconciliationLeaseService
from app.services.reconciliation_queue_service import ReconciliationQueueService
from app.services.incident_notifier import notify_incident

logger = logging.getLogger(__name__)

# Seconds after enqueue before a still-pending item gets its first retry bump
_RETRY_AFTER_AGE_SECONDS = 30.0
# Exponential backoff base (seconds) between retries
_RETRY_BASE_SECONDS = 15.0
_POLL_INTERVAL_SECONDS = 15.0
_LEASE_TTL_SECONDS = 60.0


def _worker_id() -> str:
    return f"recon_daemon_{uuid.uuid4().hex[:8]}"


async def _lock_bot_daily_state(db: AsyncSession, bot_instance_id: str) -> None:
    """Set daily trading state locked=True for bot if a record exists."""
    from sqlalchemy import select, update
    from app.models import DailyTradingState  # type: ignore[attr-defined]

    try:
        result = await db.execute(
            select(DailyTradingState).where(
                DailyTradingState.bot_instance_id == bot_instance_id,
            ).limit(1)
        )
        row = result.scalar_one_or_none()
        if row is not None and not getattr(row, "locked", False):
            row.locked = True
            row.lock_reason = "unknown_order_escalation"
            row.updated_at = datetime.now(timezone.utc)
            await db.commit()
            logger.warning(
                "reconciliation_daemon: daily lock set bot=%s reason=unknown_order_escalation",
                bot_instance_id,
            )
    except Exception as exc:
        logger.error("reconciliation_daemon: failed to lock bot %s: %s", bot_instance_id, exc)
        await db.rollback()


async def _create_critical_incident(
    db: AsyncSession,
    item: ReconciliationQueueItem,
    reason: str,
) -> None:
    """Insert a critical TradingIncident for escalated unknown order."""
    incident = TradingIncident(
        bot_instance_id=item.bot_instance_id,
        incident_type="unknown_order_escalated",
        severity="critical",
        title=f"Unknown order unresolved: {item.idempotency_key}",
        detail=(
            f"bot={item.bot_instance_id} idem={item.idempotency_key} "
            f"attempts={item.attempts} reason={reason}"
        ),
        status="open",
    )
    db.add(incident)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    await notify_incident(
        incident_type="unknown_order_escalated",
        severity="critical",
        title=incident.title,
        detail=incident.detail or "",
        payload={"bot_instance_id": item.bot_instance_id, "idempotency_key": item.idempotency_key},
    )


async def _process_item(
    db: AsyncSession,
    item: ReconciliationQueueItem,
    worker_id: str,
) -> None:
    """Process a single reconciliation queue item under lease."""
    lease_svc = ReconciliationLeaseService(db)
    queue_svc = ReconciliationQueueService(db)

    acquired = await lease_svc.try_acquire(item.id, worker_id, ttl_seconds=_LEASE_TTL_SECONDS)
    if not acquired:
        logger.debug("reconciliation_daemon: could not acquire lease for item %d", item.id)
        return

    try:
        now = datetime.now(timezone.utc)
        attempts = int(item.attempts or 0)
        max_attempts = int(item.max_attempts or 3)
        age_seconds = (now - item.created_at.replace(tzinfo=timezone.utc)).total_seconds() if item.created_at else 0.0
        deadline_passed = (
            item.deadline_at is not None
            and item.deadline_at.replace(tzinfo=timezone.utc) <= now
        )
        max_attempts_exceeded = attempts >= max_attempts

        if deadline_passed or max_attempts_exceeded:
            # Escalate: dead-letter + critical incident + lock bot
            reason = "deadline_passed" if deadline_passed else "max_attempts_exceeded"
            logger.warning(
                "reconciliation_daemon: escalating item %d bot=%s reason=%s",
                item.id,
                item.bot_instance_id,
                reason,
            )
            await queue_svc.move_to_dead_letter(
                item.bot_instance_id,
                item.idempotency_key,
                error=reason,
            )
            await _create_critical_incident(db, item, reason)
            await _lock_bot_daily_state(db, item.bot_instance_id)
            return

        if age_seconds >= _RETRY_AFTER_AGE_SECONDS:
            # Retry with exponential backoff
            backoff = _RETRY_BASE_SECONDS * (2 ** attempts)
            await queue_svc.mark_retry(
                item.bot_instance_id,
                item.idempotency_key,
                error="unknown_order_unresolved",
                retry_after_seconds=backoff,
            )
            logger.info(
                "reconciliation_daemon: item %d bot=%s retry=%d backoff=%.1fs",
                item.id,
                item.bot_instance_id,
                attempts + 1,
                backoff,
            )
        else:
            logger.debug(
                "reconciliation_daemon: item %d age=%.1fs, within grace period",
                item.id,
                age_seconds,
            )
    except Exception as exc:
        logger.error("reconciliation_daemon: error processing item %d: %s", item.id, exc)
        try:
            await db.rollback()
        except Exception:
            pass
    finally:
        try:
            await lease_svc.release(item.id, worker_id)
        except Exception as exc:
            logger.warning("reconciliation_daemon: lease release failed item %d: %s", item.id, exc)


async def _run_once(worker_id: str) -> None:
    """Run a single poll cycle."""
    async with AsyncSessionLocal() as db:
        # Clear stale leases from workers that may have died
        lease_svc = ReconciliationLeaseService(db)
        await lease_svc.release_all_expired()

        queue_svc = ReconciliationQueueService(db)
        items = await queue_svc.list_all_pending_due(limit=200)

    if not items:
        return

    logger.debug("reconciliation_daemon: processing %d items", len(items))

    for item in items:
        async with AsyncSessionLocal() as db:
            await _process_item(db, item, worker_id)


async def run_reconciliation_daemon(
    *,
    poll_interval: float = _POLL_INTERVAL_SECONDS,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Long-running daemon loop. Call from app lifespan as asyncio.Task.

    Args:
        poll_interval: Seconds between poll cycles.
        stop_event: When set, daemon stops gracefully.
    """
    worker_id = _worker_id()
    logger.info("reconciliation_daemon: started worker=%s interval=%.1fs", worker_id, poll_interval)

    while True:
        try:
            await _run_once(worker_id)
        except Exception as exc:
            logger.error("reconciliation_daemon: unhandled error in poll cycle: %s", exc)

        if stop_event is not None and stop_event.is_set():
            break

        try:
            if stop_event is not None:
                await asyncio.wait_for(
                    asyncio.shield(asyncio.ensure_future(stop_event.wait())),
                    timeout=poll_interval,
                )
                break
            else:
                await asyncio.sleep(poll_interval)
        except asyncio.TimeoutError:
            pass  # normal timeout — continue polling
        except asyncio.CancelledError:
            logger.info("reconciliation_daemon: cancelled, shutting down")
            break

    logger.info("reconciliation_daemon: stopped worker=%s", worker_id)
