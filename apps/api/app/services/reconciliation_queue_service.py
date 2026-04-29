from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ReconciliationQueueItem

# Seconds before an unknown order is escalated to dead-letter / critical incident
_DEADLINE_SECONDS = 300  # 5 minutes
_MAX_ATTEMPTS = 3


class ReconciliationQueueService:
    """Queue service for UNKNOWN order reconciliation tasks."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_item(self, bot_instance_id: str, idempotency_key: str) -> ReconciliationQueueItem | None:
        stmt = select(ReconciliationQueueItem).where(
            ReconciliationQueueItem.bot_instance_id == bot_instance_id,
            ReconciliationQueueItem.idempotency_key == idempotency_key,
        )
        return (await self.db.execute(stmt.limit(1))).scalar_one_or_none()

    async def enqueue_unknown_order(
        self,
        *,
        bot_instance_id: str,
        idempotency_key: str,
        signal_id: str | None = None,
        payload: dict[str, Any] | None = None,
        auto_commit: bool = True,
    ) -> ReconciliationQueueItem:
        stmt = select(ReconciliationQueueItem).where(
            ReconciliationQueueItem.bot_instance_id == bot_instance_id,
            ReconciliationQueueItem.idempotency_key == idempotency_key,
        )
        existing = (await self.db.execute(stmt.limit(1))).scalar_one_or_none()
        if existing is not None:
            if str(existing.status).lower() in {"resolved", "cancelled"}:
                existing.status = "pending"
            existing.payload = payload or existing.payload or {}
            existing.updated_at = datetime.now(timezone.utc)
            if auto_commit:
                await self.db.commit()
                await self.db.refresh(existing)
            else:
                await self.db.flush()
            return existing

        row = ReconciliationQueueItem(
            bot_instance_id=bot_instance_id,
            signal_id=signal_id,
            idempotency_key=idempotency_key,
            status="pending",
            attempts=0,
            max_attempts=_MAX_ATTEMPTS,
            deadline_at=datetime.now(timezone.utc) + timedelta(seconds=_DEADLINE_SECONDS),
            payload=payload or {},
        )
        self.db.add(row)
        if auto_commit:
            await self.db.commit()
            await self.db.refresh(row)
        else:
            await self.db.flush()
        return row

    async def list_pending(self, bot_instance_id: str, limit: int = 100) -> list[ReconciliationQueueItem]:
        now = datetime.now(timezone.utc)
        return (
            (
                await self.db.execute(
                    select(ReconciliationQueueItem)
                    .where(
                        ReconciliationQueueItem.bot_instance_id == bot_instance_id,
                        ReconciliationQueueItem.status.in_(["pending", "retry"]),
                        (ReconciliationQueueItem.next_retry_at.is_(None) | (ReconciliationQueueItem.next_retry_at <= now)),
                    )
                    .order_by(ReconciliationQueueItem.created_at.asc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )

    async def mark_resolved(self, bot_instance_id: str, idempotency_key: str) -> bool:
        row = (
            (
                await self.db.execute(
                    select(ReconciliationQueueItem)
                    .where(
                        ReconciliationQueueItem.bot_instance_id == bot_instance_id,
                        ReconciliationQueueItem.idempotency_key == idempotency_key,
                    )
                    .limit(1)
                )
            )
            .scalar_one_or_none()
        )
        if row is None:
            return False
        row.status = "resolved"
        row.updated_at = datetime.now(timezone.utc)
        await self.db.commit()
        return True

    async def mark_retry(
        self,
        bot_instance_id: str,
        idempotency_key: str,
        *,
        error: str,
        retry_after_seconds: float = 15.0,
    ) -> bool:
        row = (
            (
                await self.db.execute(
                    select(ReconciliationQueueItem)
                    .where(
                        ReconciliationQueueItem.bot_instance_id == bot_instance_id,
                        ReconciliationQueueItem.idempotency_key == idempotency_key,
                    )
                    .limit(1)
                )
            )
            .scalar_one_or_none()
        )
        if row is None:
            return False
        row.status = "retry"
        row.attempts = int(row.attempts or 0) + 1
        row.last_error = str(error or "")
        row.next_retry_at = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + float(retry_after_seconds), tz=timezone.utc)
        row.updated_at = datetime.now(timezone.utc)
        await self.db.commit()
        return True

    async def mark_failed_needs_operator(
        self,
        bot_instance_id: str,
        idempotency_key: str,
        *,
        error: str,
    ) -> bool:
        row = (
            (
                await self.db.execute(
                    select(ReconciliationQueueItem)
                    .where(
                        ReconciliationQueueItem.bot_instance_id == bot_instance_id,
                        ReconciliationQueueItem.idempotency_key == idempotency_key,
                    )
                    .limit(1)
                )
            )
            .scalar_one_or_none()
        )
        if row is None:
            return False
        row.status = "failed_needs_operator"
        row.attempts = int(row.attempts or 0) + 1
        row.last_error = str(error or "failed_needs_operator")
        row.next_retry_at = None
        row.updated_at = datetime.now(timezone.utc)
        await self.db.commit()
        return True

    async def has_unresolved(self, bot_instance_id: str) -> bool:
        stmt = select(ReconciliationQueueItem).where(
            ReconciliationQueueItem.bot_instance_id == bot_instance_id,
            ReconciliationQueueItem.status.in_(["pending", "retry", "in_progress", "failed_needs_operator"]),
        )
        row = (await self.db.execute(stmt.limit(1))).scalar_one_or_none()
        return row is not None

    async def list_all_pending_due(self, limit: int = 200) -> list[ReconciliationQueueItem]:
        """Return all pending/retry items due for processing across all bots.

        Used by the daemon worker which is independent of any specific bot runtime.
        """
        now = datetime.now(timezone.utc)
        return (
            (
                await self.db.execute(
                    select(ReconciliationQueueItem)
                    .where(
                        ReconciliationQueueItem.status.in_(["pending", "retry"]),
                        (
                            ReconciliationQueueItem.next_retry_at.is_(None)
                            | (ReconciliationQueueItem.next_retry_at <= now)
                        ),
                    )
                    .order_by(ReconciliationQueueItem.created_at.asc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )

    async def move_to_dead_letter(
        self,
        bot_instance_id: str,
        idempotency_key: str,
        *,
        error: str,
    ) -> bool:
        """Mark item as dead_letter (max retries exceeded or deadline passed)."""
        row = (
            (
                await self.db.execute(
                    select(ReconciliationQueueItem)
                    .where(
                        ReconciliationQueueItem.bot_instance_id == bot_instance_id,
                        ReconciliationQueueItem.idempotency_key == idempotency_key,
                    )
                    .limit(1)
                )
            )
            .scalar_one_or_none()
        )
        if row is None:
            return False
        row.status = "dead_letter"
        row.last_error = str(error or "dead_letter")
        row.next_retry_at = None
        row.lease_owner = None
        row.leased_until = None
        row.updated_at = datetime.now(timezone.utc)
        await self.db.commit()
        return True
