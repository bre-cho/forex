from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, PolicyApproval, PolicyVersion


class PolicyService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def _next_version(self, bot_instance_id: str) -> int:
        stmt = select(func.max(PolicyVersion.version)).where(PolicyVersion.bot_instance_id == bot_instance_id)
        row = (await self.db.execute(stmt)).scalar_one_or_none()
        return int(row or 0) + 1

    async def draft_policy(
        self,
        *,
        bot_instance_id: str,
        policy_snapshot: dict[str, Any],
        change_reason: str | None,
        actor_user_id: str | None,
    ) -> PolicyVersion:
        version = await self._next_version(bot_instance_id)
        row = PolicyVersion(
            bot_instance_id=bot_instance_id,
            version=version,
            policy_snapshot=policy_snapshot or {},
            change_reason=change_reason,
            status="draft",
        )
        self.db.add(row)
        self.db.add(
            PolicyApproval(
                bot_instance_id=bot_instance_id,
                policy_version=version,
                action="draft",
                actor_user_id=actor_user_id,
                note=change_reason,
            )
        )
        self.db.add(
            AuditLog(
                user_id=actor_user_id,
                action="risk_policy_draft",
                resource_type="bot_instance",
                resource_id=bot_instance_id,
                details={"version": version, "change_reason": change_reason or ""},
            )
        )
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def approve_policy(
        self,
        *,
        bot_instance_id: str,
        version: int,
        note: str | None,
        actor_user_id: str | None,
    ) -> PolicyVersion | None:
        row = (
            (
                await self.db.execute(
                    select(PolicyVersion).where(
                        PolicyVersion.bot_instance_id == bot_instance_id,
                        PolicyVersion.version == version,
                    ).limit(1)
                )
            )
            .scalar_one_or_none()
        )
        if row is None:
            return None
        row.status = "approved"
        row.approved_by = actor_user_id
        row.approved_at = datetime.now(timezone.utc)
        self.db.add(
            PolicyApproval(
                bot_instance_id=bot_instance_id,
                policy_version=version,
                action="approved",
                actor_user_id=actor_user_id,
                note=note,
            )
        )
        self.db.add(
            AuditLog(
                user_id=actor_user_id,
                action="risk_policy_approve",
                resource_type="bot_instance",
                resource_id=bot_instance_id,
                details={"version": version, "note": note or ""},
            )
        )
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def activate_policy(
        self,
        *,
        bot_instance_id: str,
        version: int,
        note: str | None,
        actor_user_id: str | None,
    ) -> PolicyVersion | None:
        row = (
            (
                await self.db.execute(
                    select(PolicyVersion).where(
                        PolicyVersion.bot_instance_id == bot_instance_id,
                        PolicyVersion.version == version,
                    ).limit(1)
                )
            )
            .scalar_one_or_none()
        )
        if row is None or row.status != "approved":
            return None

        # deactivate previous active versions
        prev_active = (
            (
                await self.db.execute(
                    select(PolicyVersion).where(
                        PolicyVersion.bot_instance_id == bot_instance_id,
                        PolicyVersion.status == "active",
                    )
                )
            )
            .scalars()
            .all()
        )
        for r in prev_active:
            r.status = "approved"

        row.status = "active"
        row.activated_by = actor_user_id
        row.activated_at = datetime.now(timezone.utc)
        self.db.add(
            PolicyApproval(
                bot_instance_id=bot_instance_id,
                policy_version=version,
                action="activated",
                actor_user_id=actor_user_id,
                note=note,
            )
        )
        self.db.add(
            AuditLog(
                user_id=actor_user_id,
                action="risk_policy_activate",
                resource_type="bot_instance",
                resource_id=bot_instance_id,
                details={"version": version, "note": note or ""},
            )
        )
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def list_versions(self, bot_instance_id: str, limit: int = 50) -> list[PolicyVersion]:
        return (
            (
                await self.db.execute(
                    select(PolicyVersion)
                    .where(PolicyVersion.bot_instance_id == bot_instance_id)
                    .order_by(PolicyVersion.version.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )

    async def get_active_policy(self, bot_instance_id: str) -> PolicyVersion | None:
        return (
            (
                await self.db.execute(
                    select(PolicyVersion)
                    .where(
                        PolicyVersion.bot_instance_id == bot_instance_id,
                        PolicyVersion.status == "active",
                    )
                    .order_by(PolicyVersion.version.desc())
                    .limit(1)
                )
            )
            .scalar_one_or_none()
        )

    async def is_policy_approved_for_live(self, bot_instance_id: str) -> bool:
        active = await self.get_active_policy(bot_instance_id)
        return active is not None and active.status == "active"
