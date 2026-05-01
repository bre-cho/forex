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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import AsyncSessionLocal
from app.core.registry import get_registry
from app.models import ReconciliationQueueItem, TradingIncident
from app.services.daily_trading_state import DailyTradingStateService
from app.services.reconciliation_lease_service import ReconciliationLeaseService
from app.services.reconciliation_queue_service import ReconciliationQueueService
from app.services.broker_connection_provider_factory import BrokerConnectionProviderFactory
from app.services.worker_heartbeat_service import WorkerHeartbeatService
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
    """Set daily trading state locked=True for bot, creating row if needed."""
    try:
        state_service = DailyTradingStateService(db)
        state = await state_service.lock_day(bot_instance_id, reason="unknown_order_escalation")
        logger.warning(
            "reconciliation_daemon: daily lock set bot=%s day=%s reason=unknown_order_escalation",
            bot_instance_id,
            getattr(state, "trading_day", None),
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
    dedupe_key = f"unknown_order_escalated:{item.bot_instance_id}:{item.idempotency_key}:{reason}"
    existing = (
        (
            await db.execute(
                select(TradingIncident)
                .where(
                    TradingIncident.bot_instance_id == item.bot_instance_id,
                    TradingIncident.incident_type == "unknown_order_escalated",
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
        bot_instance_id=item.bot_instance_id,
        incident_type="unknown_order_escalated",
        severity="critical",
        title=f"Unknown order unresolved: {item.idempotency_key}",
        detail=(
            f"bot={item.bot_instance_id} idem={item.idempotency_key} "
            f"attempts={item.attempts} reason={reason} dedupe_key={dedupe_key}"
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


async def _attempt_broker_reconcile(item: ReconciliationQueueItem) -> dict[str, object]:
    """Try broker-side UNKNOWN reconciliation for a queue item.

        Returns a dict payload with keys:
            - resolved: bool (filled/rejected conclusive)
            - outcome: reconciler outcome string
            - resolution_code: normalized reason code
    """
    try:
        registry = get_registry()
        provider_from_runtime = False
        provider = None
        if registry is None or not hasattr(registry, "get"):
            runtime = None
        else:
            runtime = registry.get(str(item.bot_instance_id))
        if runtime is not None:
            provider = getattr(runtime, "broker_provider", None)
            provider_from_runtime = provider is not None
        if provider is None:
            # Runtime may be down. Reconstruct provider from DB credentials.
            async with AsyncSessionLocal() as fac_db:
                factory = BrokerConnectionProviderFactory(fac_db)
                provider = await factory.create_provider_for_bot(str(item.bot_instance_id))
        if provider is None:
            return False

        if not bool(getattr(provider, "is_connected", False)) and callable(getattr(provider, "connect", None)):
            await provider.connect()

        from execution_service.unknown_order_reconciler import UnknownOrderReconciler

        reconciler = UnknownOrderReconciler(
            provider=provider,
            max_retries=1,
            retry_interval_seconds=0.0,
        )
        result = await reconciler.resolve_unknown_order(
            bot_instance_id=str(item.bot_instance_id),
            idempotency_key=str(item.idempotency_key),
            signal_id=str(item.signal_id or ""),
        )
        outcome = str(result.outcome or "").lower()
        resolution_code = str((result.details or {}).get("resolution_code") or outcome or "unknown")
        provider_name = str(getattr(provider, "provider_name", "") or "unknown")

        try:
            from app.services.reconciliation_attempt_event_service import ReconciliationAttemptEventService

            async with AsyncSessionLocal() as attempt_db:
                event_svc = ReconciliationAttemptEventService(attempt_db)
                await event_svc.record_attempt(
                    queue_item_id=int(getattr(item, "id", 0) or 0) or None,
                    bot_instance_id=str(item.bot_instance_id),
                    signal_id=str(item.signal_id or "") or None,
                    idempotency_key=str(item.idempotency_key),
                    worker_id=str(getattr(item, "lease_owner", "") or None),
                    attempt_no=int(getattr(item, "attempts", 0) or 0) + 1,
                    outcome=outcome,
                    resolution_code=resolution_code,
                    provider=provider_name,
                    payload=result.to_dict(),
                )
        except Exception as persist_exc:
            logger.warning(
                "reconciliation_daemon: failed to persist attempt event item=%s bot=%s: %s",
                getattr(item, "id", None),
                getattr(item, "bot_instance_id", None),
                persist_exc,
            )

        conclusive = outcome in {"filled", "rejected"}
        if not conclusive:
            return {
                "resolved": False,
                "outcome": outcome,
                "resolution_code": resolution_code,
            }

        # Persist broker truth into order ledger BEFORE changing queue status.
        # If this fails, queue stays retry — no silent data loss.
        try:
            from app.services.order_ledger_service import OrderLedgerService

            async with AsyncSessionLocal() as persist_db:
                ledger_svc = OrderLedgerService(persist_db)
                mapped_event = "order_filled" if result.outcome == "filled" else "order_rejected"
                await ledger_svc.record_lifecycle_event(
                    bot_instance_id=str(item.bot_instance_id),
                    event_type=mapped_event,
                    idempotency_key=str(item.idempotency_key),
                    broker=str(getattr(provider, "provider_name", "") or "unknown"),
                    payload={
                        **result.to_dict(),
                        "signal_id": str(item.signal_id or ""),
                        "broker_order_id": str(result.broker_order_id or "") or None,
                        "broker_position_id": str(result.broker_position_id or "") or None,
                        "broker_deal_id": str(result.broker_deal_id or "") or None,
                        "raw_response_hash": str(result.raw_response_hash or "") or None,
                        "avg_fill_price": float(result.fill_price) if result.fill_price else None,
                        "filled_volume": float(result.fill_volume) if result.fill_volume else None,
                    },
                )
        except Exception as persist_exc:
            logger.error(
                "reconciliation_daemon: ledger persist failed before mark_resolved item=%s bot=%s: %s",
                getattr(item, "id", None),
                getattr(item, "bot_instance_id", None),
                persist_exc,
            )
            return {
                "resolved": False,
                "outcome": "lookup_failed",
                "resolution_code": "ledger_persist_failed",
            }

        return {
            "resolved": True,
            "outcome": outcome,
            "resolution_code": resolution_code,
        }
    except Exception as exc:
        logger.warning(
            "reconciliation_daemon: broker reconcile failed item=%s bot=%s: %s",
            getattr(item, "id", None),
            getattr(item, "bot_instance_id", None),
            exc,
        )
        return {
            "resolved": False,
            "outcome": "lookup_failed",
            "resolution_code": "daemon_exception",
        }
    finally:
        try:
            # Disconnect only when provider was created by daemon fallback (not runtime-owned).
            if "provider" in locals() and provider is not None and not bool(locals().get("provider_from_runtime", False)):
                disconnect = getattr(provider, "disconnect", None)
                if callable(disconnect):
                    await disconnect()
        except Exception:
            pass


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

        # Try broker source-of-truth reconciliation before applying retry/escalation.
        if age_seconds >= _RETRY_AFTER_AGE_SECONDS:
            broker_attempt = await _attempt_broker_reconcile(item)
            if isinstance(broker_attempt, dict):
                broker_resolved = bool(broker_attempt.get("resolved", False))
                resolution_code = str(broker_attempt.get("resolution_code") or "unknown")
            else:
                broker_resolved = bool(broker_attempt)
                resolution_code = "unknown"
            if broker_resolved:
                await queue_svc.mark_resolved(item.bot_instance_id, item.idempotency_key)
                logger.info(
                    "reconciliation_daemon: resolved via broker item %d bot=%s",
                    item.id,
                    item.bot_instance_id,
                )
            if RECONCILIATION_RESOLVED_TOTAL is not None:
                try:
                    RECONCILIATION_RESOLVED_TOTAL.labels(
                        bot_id=str(item.bot_instance_id), provider="unknown", resolution_code=resolution_code
                    ).inc()
                except Exception:
                    pass
                return
        else:
            resolution_code = "grace_period"

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
                resolution_code=resolution_code,
            )
            await _create_critical_incident(db, item, reason)
            await _lock_bot_daily_state(db, item.bot_instance_id)
            if RECONCILIATION_OVERDUE_TOTAL is not None:
                try:
                    RECONCILIATION_OVERDUE_TOTAL.labels(bot_id=str(item.bot_instance_id)).inc()
                except Exception:
                    pass
            return

        if age_seconds >= _RETRY_AFTER_AGE_SECONDS:
            # Retry with exponential backoff
            backoff = _RETRY_BASE_SECONDS * (2 ** attempts)
            await queue_svc.mark_retry(
                item.bot_instance_id,
                item.idempotency_key,
                error="unknown_order_unresolved",
                resolution_code=resolution_code,
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
        try:
            hb = WorkerHeartbeatService(db)
            await hb.beat(
                worker_name="reconciliation_daemon",
                worker_id=worker_id,
                status="running",
                detail={"phase": "poll"},
            )
        except Exception:
            pass

        # Clear stale leases from workers that may have died
        lease_svc = ReconciliationLeaseService(db)
        await lease_svc.release_all_expired()

        queue_svc = ReconciliationQueueService(db)
        items = await queue_svc.list_all_pending_due(limit=200)
        if RECONCILIATION_QUEUE_DEPTH is not None:
            try:
                RECONCILIATION_QUEUE_DEPTH.set(len(items))
            except Exception:
                pass

    if not items:
        return

    logger.debug("reconciliation_daemon: processing %d items", len(items))

    for item in items:
        async with AsyncSessionLocal() as db:
            await _process_item(db, item, worker_id)


# Module-level flag checked by /health/live-hard endpoint.
_daemon_running: bool = False


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
    global _daemon_running
    worker_id = _worker_id()
    logger.info("reconciliation_daemon: started worker=%s interval=%.1fs", worker_id, poll_interval)
    _daemon_running = True
    try:
        async with AsyncSessionLocal() as db:
            hb = WorkerHeartbeatService(db)
            await hb.beat(
                worker_name="reconciliation_daemon",
                worker_id=worker_id,
                status="running",
                detail={"phase": "start", "poll_interval": poll_interval},
            )
    except Exception:
        pass

    try:
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
    finally:
        _daemon_running = False
        try:
            async with AsyncSessionLocal() as db:
                hb = WorkerHeartbeatService(db)
                await hb.beat(
                    worker_name="reconciliation_daemon",
                    worker_id=worker_id,
                    status="stopped",
                    detail={"phase": "stop"},
                )
        except Exception:
            pass

    logger.info("reconciliation_daemon: stopped worker=%s", worker_id)
