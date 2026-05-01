from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.permissions import role_level
from app.models import ActionApprovalRequest, AuditLog, User, WorkspaceMember


ACTION_POLICY: dict[str, dict[str, str]] = {
    "start_live_bot": {"requester": "operator", "approver": "risk_admin"},
    "increase_risk_pct": {"requester": "trader", "approver": "risk_admin"},
    "unlock_daily_lock": {"requester": "operator", "approver": "risk_admin"},
    "disable_kill_switch": {"requester": "operator", "approver": "risk_admin"},
    "change_provider_credential": {"requester": "operator", "approver": "super_admin"},
    "retry_unknown_order": {"requester": "operator", "approver": "risk_admin"},
}


class ActionApprovalService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def _workspace_role_for_user(self, workspace_id: str, user_id: str) -> str | None:
        row = (
            (
                await self.db.execute(
                    select(WorkspaceMember).where(
                        WorkspaceMember.workspace_id == workspace_id,
                        WorkspaceMember.user_id == user_id,
                    ).limit(1)
                )
            )
            .scalar_one_or_none()
        )
        if row is None:
            return None
        return str(getattr(row, "role", "") or "").lower()

    async def _assert_role(self, *, workspace_id: str, user: User, required_role: str) -> None:
        if bool(getattr(user, "is_superuser", False)):
            return
        user_id = str(getattr(user, "id", "") or "")
        workspace_role = await self._workspace_role_for_user(workspace_id, user_id)
        if workspace_role is None:
            raise HTTPException(status_code=403, detail="Not a member")
        if role_level(workspace_role) < role_level(required_role):
            raise HTTPException(status_code=403, detail=f"Requires {required_role} role or above")

    async def create_request(
        self,
        *,
        workspace_id: str,
        bot_instance_id: str | None,
        action_type: str,
        reason: str,
        request_payload: dict[str, Any] | None,
        actor: User,
        expires_in_minutes: int | None = None,
    ) -> ActionApprovalRequest:
        policy = ACTION_POLICY.get(action_type)
        if policy is None:
            raise HTTPException(status_code=400, detail="unsupported_action_type")
        await self._assert_role(
            workspace_id=workspace_id,
            user=actor,
            required_role=policy["requester"],
        )
        if not reason.strip():
            raise HTTPException(status_code=400, detail="approval_reason_required")

        expires_at = None
        if expires_in_minutes is not None and int(expires_in_minutes) > 0:
            expires_at = datetime.now(timezone.utc) + timedelta(minutes=int(expires_in_minutes))

        row = ActionApprovalRequest(
            workspace_id=workspace_id,
            bot_instance_id=bot_instance_id,
            action_type=action_type,
            status="pending",
            requested_by_user_id=str(getattr(actor, "id", "") or "") or None,
            reason=reason.strip(),
            request_payload=dict(request_payload or {}),
            expires_at=expires_at,
        )
        self.db.add(row)
        await self.db.flush()
        self.db.add(
            AuditLog(
                user_id=str(getattr(actor, "id", "") or "") or None,
                action="approval_request_created",
                resource_type="action_approval_request",
                resource_id=str(row.id),
                details={
                    "workspace_id": workspace_id,
                    "bot_instance_id": bot_instance_id,
                    "action_type": action_type,
                    "reason": reason.strip(),
                },
            )
        )
        return row

    async def decide_request(
        self,
        *,
        approval_id: int,
        workspace_id: str,
        decision: str,
        note: str | None,
        actor: User,
    ) -> ActionApprovalRequest:
        row = (
            (
                await self.db.execute(
                    select(ActionApprovalRequest).where(
                        ActionApprovalRequest.id == approval_id,
                        ActionApprovalRequest.workspace_id == workspace_id,
                    ).limit(1)
                )
            )
            .scalar_one_or_none()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="approval_request_not_found")
        if row.status != "pending":
            raise HTTPException(status_code=409, detail="approval_request_not_pending")

        policy = ACTION_POLICY.get(str(row.action_type))
        if policy is None:
            raise HTTPException(status_code=400, detail="unsupported_action_type")
        await self._assert_role(
            workspace_id=workspace_id,
            user=actor,
            required_role=policy["approver"],
        )

        now = datetime.now(timezone.utc)
        if decision == "approve":
            row.status = "approved"
            row.approved_by_user_id = str(getattr(actor, "id", "") or "") or None
        elif decision == "reject":
            row.status = "rejected"
            row.rejected_by_user_id = str(getattr(actor, "id", "") or "") or None
        else:
            raise HTTPException(status_code=400, detail="unsupported_decision")

        row.decision_note = str(note or "").strip() or None
        row.decided_at = now

        self.db.add(
            AuditLog(
                user_id=str(getattr(actor, "id", "") or "") or None,
                action=f"approval_request_{decision}d",
                resource_type="action_approval_request",
                resource_id=str(row.id),
                details={
                    "workspace_id": workspace_id,
                    "action_type": str(row.action_type),
                    "decision_note": row.decision_note or "",
                },
            )
        )
        return row

    async def list_requests(
        self,
        *,
        workspace_id: str,
        status_filter: str | None,
        action_type: str | None,
        limit: int,
    ) -> list[ActionApprovalRequest]:
        stmt = select(ActionApprovalRequest).where(ActionApprovalRequest.workspace_id == workspace_id)
        if status_filter:
            stmt = stmt.where(ActionApprovalRequest.status == status_filter)
        if action_type:
            stmt = stmt.where(ActionApprovalRequest.action_type == action_type)
        stmt = stmt.order_by(ActionApprovalRequest.created_at.desc()).limit(max(1, min(int(limit), 200)))
        return ((await self.db.execute(stmt)).scalars().all())

    async def validate_and_consume_approval(
        self,
        *,
        approval_id: int | None,
        workspace_id: str,
        action_type: str,
        bot_instance_id: str | None,
        actor_user_id: str | None,
    ) -> ActionApprovalRequest:
        if approval_id is None:
            raise HTTPException(status_code=403, detail=f"approval_required:{action_type}")

        row = (
            (
                await self.db.execute(
                    select(ActionApprovalRequest).where(
                        ActionApprovalRequest.id == int(approval_id),
                        ActionApprovalRequest.workspace_id == workspace_id,
                    ).limit(1)
                )
            )
            .scalar_one_or_none()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="approval_request_not_found")
        if str(row.action_type) != action_type:
            raise HTTPException(status_code=403, detail=f"approval_action_mismatch:{action_type}")
        if row.bot_instance_id and bot_instance_id and str(row.bot_instance_id) != str(bot_instance_id):
            raise HTTPException(status_code=403, detail="approval_bot_mismatch")
        if str(row.status) != "approved":
            raise HTTPException(status_code=403, detail="approval_not_approved")
        if row.consumed_at is not None:
            raise HTTPException(status_code=409, detail="approval_already_consumed")

        now = datetime.now(timezone.utc)
        if row.expires_at is not None and row.expires_at <= now:
            raise HTTPException(status_code=403, detail="approval_expired")

        row.status = "consumed"
        row.consumed_at = now
        row.consumed_by_user_id = actor_user_id
        self.db.add(
            AuditLog(
                user_id=actor_user_id,
                action="approval_request_consumed",
                resource_type="action_approval_request",
                resource_id=str(row.id),
                details={
                    "workspace_id": workspace_id,
                    "action_type": action_type,
                    "bot_instance_id": bot_instance_id,
                },
            )
        )
        return row
