from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ProviderCertification


DEFAULT_REQUIRED_CHECKS: list[str] = [
    "account_authorized",
    "account_id_match",
    "quote_realtime",
    "server_time_valid",
    "instrument_spec_valid",
    "margin_estimate_valid",
    "client_order_id_supported",
    "order_lookup_supported",
    "execution_lookup_supported",
    "close_all_supported",
    "reconciliation_roundtrip_passed",
]


class ProviderCertificationService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    @staticmethod
    def _canonical_hash(payload: dict[str, Any]) -> str:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize_required_checks(required_checks: Iterable[str] | None) -> list[str]:
        values = [str(v).strip() for v in (required_checks or DEFAULT_REQUIRED_CHECKS)]
        deduped = sorted({v for v in values if v})
        return deduped or list(DEFAULT_REQUIRED_CHECKS)

    @staticmethod
    def _evaluate_checks(required_checks: list[str], checks: dict[str, Any] | None) -> tuple[bool, list[str]]:
        normalized = {str(k): bool(v) for k, v in dict(checks or {}).items()}
        passed = [k for k in required_checks if normalized.get(k, False)]
        is_live_certified = all(k in passed for k in required_checks)
        return is_live_certified, passed

    async def record_certification(
        self,
        *,
        bot_instance_id: str,
        provider: str,
        mode: str,
        account_id: str | None,
        symbol: str | None,
        checks: dict[str, Any] | None,
        evidence: dict[str, Any] | None,
        required_checks: Iterable[str] | None = None,
        actor_user_id: str | None = None,
    ) -> ProviderCertification:
        req = self._normalize_required_checks(required_checks)
        live_certified, checks_passed = self._evaluate_checks(req, checks)

        proof_payload = {
            "bot_instance_id": str(bot_instance_id),
            "provider": str(provider),
            "mode": str(mode),
            "account_id": str(account_id or ""),
            "symbol": str(symbol or ""),
            "required_checks": list(req),
            "checks": dict(checks or {}),
            "checks_passed": list(checks_passed),
            "evidence": dict(evidence or {}),
            "actor_user_id": str(actor_user_id or ""),
        }
        cert_hash = self._canonical_hash(proof_payload)
        now = datetime.now(timezone.utc)

        row = ProviderCertification(
            bot_instance_id=str(bot_instance_id),
            provider=str(provider),
            mode=str(mode),
            account_id=(str(account_id) if account_id else None),
            symbol=(str(symbol) if symbol else None),
            live_certified=bool(live_certified),
            certification_hash=cert_hash,
            required_checks=list(req),
            checks_passed=list(checks_passed),
            checks=dict(checks or {}),
            evidence=dict(evidence or {}),
            actor_user_id=(str(actor_user_id) if actor_user_id else None),
            certified_at=(now if live_certified else None),
        )
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def get_latest(
        self,
        *,
        bot_instance_id: str,
        provider: str,
        mode: str = "live",
        account_id: str | None = None,
    ) -> ProviderCertification | None:
        stmt = (
            select(ProviderCertification)
            .where(
                ProviderCertification.bot_instance_id == str(bot_instance_id),
                ProviderCertification.provider == str(provider),
                ProviderCertification.mode == str(mode),
            )
            .order_by(ProviderCertification.id.desc())
            .limit(1)
        )
        if account_id:
            stmt = stmt.where(ProviderCertification.account_id == str(account_id))
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def is_live_certified(
        self,
        *,
        bot_instance_id: str,
        provider: str,
        account_id: str | None = None,
    ) -> bool:
        row = await self.get_latest(
            bot_instance_id=bot_instance_id,
            provider=provider,
            mode="live",
            account_id=account_id,
        )
        return bool(row and row.live_certified)

    async def list_for_bot(self, *, bot_instance_id: str, limit: int = 50) -> list[ProviderCertification]:
        stmt = (
            select(ProviderCertification)
            .where(ProviderCertification.bot_instance_id == str(bot_instance_id))
            .order_by(ProviderCertification.id.desc())
            .limit(max(1, min(int(limit), 500)))
        )
        rows = (await self.db.execute(stmt)).scalars().all()
        return list(rows)
