from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.permissions import require_workspace_role
from app.models import User
from app.services.action_approval_service import ACTION_POLICY, ActionApprovalService

router = APIRouter(prefix="/v1/workspaces/{workspace_id}/approvals", tags=["action-approvals"])


@router.get("/policy")
async def get_action_approval_policy(
    workspace_id: str,
    current_user: User = Depends(get_current_user),
    _member=Depends(require_workspace_role("viewer")),
):
    return ACTION_POLICY


@router.get("")
async def list_approvals(
    workspace_id: str,
    status: str | None = None,
    action_type: str | None = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _member=Depends(require_workspace_role("viewer")),
):
    svc = ActionApprovalService(db)
    return await svc.list_requests(
        workspace_id=workspace_id,
        status_filter=str(status or "").strip() or None,
        action_type=str(action_type or "").strip() or None,
        limit=limit,
    )


@router.post("")
async def create_approval_request(
    workspace_id: str,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _member=Depends(require_workspace_role("operator")),
):
    svc = ActionApprovalService(db)
    return await svc.create_request(
        workspace_id=workspace_id,
        bot_instance_id=str((payload or {}).get("bot_id") or "").strip() or None,
        action_type=str((payload or {}).get("action_type") or "").strip(),
        reason=str((payload or {}).get("reason") or "").strip(),
        request_payload=dict((payload or {}).get("request_payload") or {}),
        actor=current_user,
        expires_in_minutes=(payload or {}).get("expires_in_minutes"),
    )


@router.post("/{approval_id}/approve")
async def approve_request(
    workspace_id: str,
    approval_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _member=Depends(require_workspace_role("risk_admin")),
):
    svc = ActionApprovalService(db)
    return await svc.decide_request(
        approval_id=approval_id,
        workspace_id=workspace_id,
        decision="approve",
        note=str((payload or {}).get("note") or "").strip() or None,
        actor=current_user,
    )


@router.post("/{approval_id}/reject")
async def reject_request(
    workspace_id: str,
    approval_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _member=Depends(require_workspace_role("risk_admin")),
):
    svc = ActionApprovalService(db)
    return await svc.decide_request(
        approval_id=approval_id,
        workspace_id=workspace_id,
        decision="reject",
        note=str((payload or {}).get("note") or "").strip() or None,
        actor=current_user,
    )
