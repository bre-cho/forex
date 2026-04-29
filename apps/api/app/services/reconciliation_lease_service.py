"""Lease service for reconciliation queue items.

Uses optimistic DB-level locking: only grants lease if leased_until < now (expired or null)
and status is not terminal.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ReconciliationQueueItem

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {"resolved", "dead_letter", "cancelled"}


class ReconciliationLeaseService:
    """Acquire and release processing leases on reconciliation queue items.

    A lease prevents two daemon workers from double-processing the same item.
    Leases are time-bounded (TTL) — an expired lease is automatically available.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def try_acquire(
        self,
        item_id: int,
        worker_id: str,
        ttl_seconds: float = 60.0,
    ) -> bool:
        """Try to acquire lease on item.  Returns True if lease granted."""
        now = datetime.now(timezone.utc)
        leased_until = now + timedelta(seconds=ttl_seconds)

        # Only acquire if not leased (or lease expired) and status non-terminal
        result = await self.db.execute(
            select(ReconciliationQueueItem)
            .where(
                ReconciliationQueueItem.id == item_id,
                ReconciliationQueueItem.status.notin_(list(_TERMINAL_STATUSES)),
                (
                    ReconciliationQueueItem.leased_until.is_(None)
                    | (ReconciliationQueueItem.leased_until <= now)
                ),
            )
            .with_for_update(skip_locked=True)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return False

        row.lease_owner = worker_id
        row.leased_until = leased_until
        row.updated_at = now
        await self.db.commit()
        return True

    async def release(self, item_id: int, worker_id: str) -> None:
        """Release lease (only if held by this worker)."""
        now = datetime.now(timezone.utc)
        result = await self.db.execute(
            select(ReconciliationQueueItem)
            .where(
                ReconciliationQueueItem.id == item_id,
                ReconciliationQueueItem.lease_owner == worker_id,
            )
            .with_for_update(skip_locked=True)
        )
        row = result.scalar_one_or_none()
        if row is not None:
            row.lease_owner = None
            row.leased_until = None
            row.updated_at = now
            await self.db.commit()

    async def release_all_expired(self) -> int:
        """Clear stale leases (expired leased_until) — call on daemon startup."""
        now = datetime.now(timezone.utc)
        result = await self.db.execute(
            select(ReconciliationQueueItem).where(
                ReconciliationQueueItem.leased_until.isnot(None),
                ReconciliationQueueItem.leased_until <= now,
            )
        )
        rows = result.scalars().all()
        count = 0
        for row in rows:
            row.lease_owner = None
            row.leased_until = None
            count += 1
        if count:
            await self.db.commit()
            logger.info("reconciliation_daemon: cleared %d stale leases", count)
        return count
