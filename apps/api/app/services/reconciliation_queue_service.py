from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ReconciliationQueueItem


class ReconciliationQueueService:
    """Queue service for UNKNOWN order reconciliation tasks."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def enqueue_unknown_order(
        self,
        *,
        bot_instance_id: str,
        idempotency_key: str,
        signal_id: str | None = None,
        payload: dict[str, Any] | None = None,
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
            await self.db.commit()
            await self.db.refresh(existing)
            return existing

        row = ReconciliationQueueItem(
            bot_instance_id=bot_instance_id,
            signal_id=signal_id,
            idempotency_key=idempotency_key,
            status="pending",
            attempts=0,
            payload=payload or {},
        )
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
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

    async def has_unresolved(self, bot_instance_id: str) -> bool:
        stmt = select(ReconciliationQueueItem).where(
            ReconciliationQueueItem.bot_instance_id == bot_instance_id,
            ReconciliationQueueItem.status.in_(["pending", "retry", "in_progress"]),
        )
        row = (await self.db.execute(stmt.limit(1))).scalar_one_or_none()
        return row is not None
