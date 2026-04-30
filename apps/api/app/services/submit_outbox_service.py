from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SubmitOutbox, SubmitOutboxEvent


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
        payload = dict(phase_payload or {})
        payload_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
        ).hexdigest()
        if row is None:
            row = SubmitOutbox(
                bot_instance_id=bot_instance_id,
                idempotency_key=idempotency_key,
                phase=phase,
                request_hash=request_hash,
                provider=provider,
                phase_payload=payload,
                created_at=now,
                updated_at=now,
            )
            self.db.add(row)
        else:
            row.phase = phase
            row.request_hash = request_hash
            row.provider = provider
            row.phase_payload = payload
            row.updated_at = now

        # Append-only immutable event history for every phase transition.
        event = SubmitOutboxEvent(
            bot_instance_id=bot_instance_id,
            idempotency_key=idempotency_key,
            phase=phase,
            request_hash=request_hash,
            payload_hash=payload_hash,
            provider=provider,
            phase_payload=payload,
            created_at=now,
        )
        self.db.add(event)

        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def list_stale_submit_phases(
        self,
        *,
        older_than_seconds: float,
        phases: tuple[str, ...] = ("SUBMITTING", "BROKER_SEND_STARTED"),
        limit: int = 200,
    ) -> list[SubmitOutbox]:
        now = datetime.now(timezone.utc)
        cutoff = datetime.fromtimestamp(now.timestamp() - float(older_than_seconds), tz=timezone.utc)
        return (
            (
                await self.db.execute(
                    select(SubmitOutbox)
                    .where(
                        SubmitOutbox.phase.in_(list(phases)),
                        SubmitOutbox.updated_at <= cutoff,
                    )
                    .order_by(SubmitOutbox.updated_at.asc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
