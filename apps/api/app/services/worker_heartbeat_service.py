from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import WorkerHeartbeat


class WorkerHeartbeatService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def beat(
        self,
        *,
        worker_name: str,
        worker_id: str,
        status: str = "running",
        detail: dict[str, Any] | None = None,
    ) -> WorkerHeartbeat:
        row = (
            (
                await self.db.execute(
                    select(WorkerHeartbeat)
                    .where(
                        WorkerHeartbeat.worker_name == worker_name,
                        WorkerHeartbeat.worker_id == worker_id,
                    )
                    .limit(1)
                )
            )
            .scalar_one_or_none()
        )
        now = datetime.now(timezone.utc)
        if row is None:
            row = WorkerHeartbeat(
                worker_name=worker_name,
                worker_id=worker_id,
                status=status,
                detail=dict(detail or {}),
                last_heartbeat_at=now,
                created_at=now,
                updated_at=now,
            )
            self.db.add(row)
        else:
            row.status = status
            row.detail = dict(detail or {})
            row.last_heartbeat_at = now
            row.updated_at = now

        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def is_worker_healthy(
        self,
        *,
        worker_name: str,
        max_age_seconds: float = 60.0,
    ) -> bool:
        row = (
            (
                await self.db.execute(
                    select(WorkerHeartbeat)
                    .where(WorkerHeartbeat.worker_name == worker_name)
                    .order_by(desc(WorkerHeartbeat.last_heartbeat_at))
                    .limit(1)
                )
            )
            .scalar_one_or_none()
        )
        if row is None:
            return False
        if str(row.status or "").lower() != "running":
            return False
        hb_ts = row.last_heartbeat_at
        if hb_ts is None:
            return False
        if hb_ts.tzinfo is None:
            hb_ts = hb_ts.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - hb_ts).total_seconds() <= float(max_age_seconds)
