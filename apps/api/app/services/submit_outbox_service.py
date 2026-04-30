from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SubmitOutbox


class SubmitOutboxService:
    """Tracks broker submit phases for each idempotency key."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def mark_phase(
        self,
        *,
        bot_instance_id: str,
        idempotency_key: str,
        phase: str,
        request_hash: str | None,
        provider: str | None,
        phase_payload: dict[str, Any] | None = None,
    ) -> SubmitOutbox:
        row = (
            (
                await self.db.execute(
                    select(SubmitOutbox)
                    .where(
                        SubmitOutbox.bot_instance_id == bot_instance_id,
                        SubmitOutbox.idempotency_key == idempotency_key,
                    )
                    .limit(1)
                )
            )
            .scalar_one_or_none()
        )

        now = datetime.now(timezone.utc)
        if row is None:
            row = SubmitOutbox(
                bot_instance_id=bot_instance_id,
                idempotency_key=idempotency_key,
                phase=phase,
                request_hash=request_hash,
                provider=provider,
                phase_payload=dict(phase_payload or {}),
                created_at=now,
                updated_at=now,
            )
            self.db.add(row)
        else:
            row.phase = phase
            row.request_hash = request_hash
            row.provider = provider
            row.phase_payload = dict(phase_payload or {})
            row.updated_at = now

        await self.db.commit()
        await self.db.refresh(row)
        return row
