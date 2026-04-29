from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.dependencies.auth import get_current_user
from app.models import User
from app.services.policy_service import PolicyService

router = APIRouter(prefix="/v1/workspaces/{workspace_id}/bots/{bot_id}/risk-policy", tags=["risk-policy"])


@router.get("/versions")
async def list_policy_versions(
    workspace_id: str,
    bot_id: str,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc = PolicyService(db)
    return await svc.list_versions(bot_id, limit)


@router.get("/active")
async def get_active_policy(
    workspace_id: str,
    bot_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc = PolicyService(db)
    row = await svc.get_active_policy(bot_id)
    if row is None:
        return {"bot_instance_id": bot_id, "active": None, "approved_for_live": False}
    return {"bot_instance_id": bot_id, "active": row, "approved_for_live": True}


@router.post("/draft")
async def draft_policy(
    workspace_id: str,
    bot_id: str,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    policy_snapshot = payload.get("policy_snapshot")
    if not isinstance(policy_snapshot, dict):
        raise HTTPException(status_code=400, detail="policy_snapshot must be an object")
    change_reason = str(payload.get("change_reason") or "") or None
    svc = PolicyService(db)
    row = await svc.draft_policy(
        bot_instance_id=bot_id,
        policy_snapshot=policy_snapshot,
        change_reason=change_reason,
        actor_user_id=str(getattr(current_user, "id", "") or "") or None,
    )
    return {"status": "drafted", "version": row.version, "policy": row}


@router.post("/approve")
async def approve_policy(
    workspace_id: str,
    bot_id: str,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not bool(getattr(current_user, "is_superuser", False)):
        raise HTTPException(status_code=403, detail="Admin permission required")
    version = int(payload.get("version") or 0)
    if version <= 0:
        raise HTTPException(status_code=400, detail="version must be > 0")
    note = str(payload.get("note") or "") or None
    svc = PolicyService(db)
    row = await svc.approve_policy(
        bot_instance_id=bot_id,
        version=version,
        note=note,
        actor_user_id=str(getattr(current_user, "id", "") or "") or None,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Policy version not found")
    return {"status": "approved", "version": row.version, "policy": row}


@router.post("/activate")
async def activate_policy(
    workspace_id: str,
    bot_id: str,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not bool(getattr(current_user, "is_superuser", False)):
        raise HTTPException(status_code=403, detail="Admin permission required")
    version = int(payload.get("version") or 0)
    if version <= 0:
        raise HTTPException(status_code=400, detail="version must be > 0")
    note = str(payload.get("note") or "") or None
    svc = PolicyService(db)
    row = await svc.activate_policy(
        bot_instance_id=bot_id,
        version=version,
        note=note,
        actor_user_id=str(getattr(current_user, "id", "") or "") or None,
    )
    if row is None:
        raise HTTPException(status_code=400, detail="Policy version must be approved before activation")
    return {"status": "activated", "version": row.version, "policy": row}
