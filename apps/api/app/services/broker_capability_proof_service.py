from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


class BrokerCapabilityProofService:
    """Persist broker capability proof snapshots for live-start auditability.

    Uses AuditLog to avoid schema coupling while still keeping immutable proof records.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def record_proof(
        self,
        *,
        bot_instance_id: str,
        provider: str,
        account_id: str | None,
        symbol: str,
        timeframe: str | None,
        proof_payload: dict[str, Any],
    ) -> str:
        canonical = json.dumps(proof_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        proof_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        row = AuditLog(
            action="broker_capability_proof",
            resource_type="bot",
            resource_id=str(bot_instance_id),
            details={
                "provider": str(provider),
                "account_id": str(account_id or ""),
                "symbol": str(symbol),
                "timeframe": str(timeframe or ""),
                "proof_hash": proof_hash,
                "proof": dict(proof_payload or {}),
            },
        )
        self.db.add(row)
        await self.db.commit()
        return proof_hash
