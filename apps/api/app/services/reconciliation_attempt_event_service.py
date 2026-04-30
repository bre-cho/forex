from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ReconciliationAttemptEvent


class ReconciliationAttemptEventService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def record_attempt(
        self,
        *,
        queue_item_id: int | None,
        bot_instance_id: str,
        signal_id: str | None,
        idempotency_key: str,
        worker_id: str | None,
        attempt_no: int,
        outcome: str,
        resolution_code: str | None,
        provider: str | None,
        payload: dict[str, Any] | None,
        auto_commit: bool = True,
    ) -> ReconciliationAttemptEvent:
        payload_data = dict(payload or {})
        payload_hash = hashlib.sha256(
            json.dumps(payload_data, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
        ).hexdigest()
        row = ReconciliationAttemptEvent(
            queue_item_id=int(queue_item_id) if queue_item_id is not None else None,
            bot_instance_id=str(bot_instance_id),
            signal_id=str(signal_id or "") or None,
            idempotency_key=str(idempotency_key),
            worker_id=str(worker_id or "") or None,
            attempt_no=int(attempt_no),
            outcome=str(outcome),
            resolution_code=str(resolution_code or "") or None,
            provider=str(provider or "") or None,
            payload_hash=payload_hash,
            payload=payload_data,
            created_at=datetime.now(timezone.utc),
        )
        self.db.add(row)
        if auto_commit:
            await self.db.commit()
            await self.db.refresh(row)
        else:
            await self.db.flush()
        return row
