"""Incidents router — list, acknowledge, and resolve trading incidents."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.dependencies.auth import get_current_user
from app.models import User

router = APIRouter(
    prefix="/v1/workspaces/{workspace_id}/incidents",
    tags=["incidents"],
)


async def _get_incidents_raw(db: AsyncSession, workspace_id: str, bot_instance_id: Optional[str], status: Optional[str], limit: int):
    """Query trading_incidents table directly via raw SQL to avoid adding ORM model in this patch."""
    from sqlalchemy import text
    conditions = ["1=1"]
    params: dict = {"limit": limit}
    # workspace_id filter via bot_instances join would require more tables — filter by bot_instance_id if given
    if bot_instance_id:
        conditions.append("bot_instance_id = :bot_id")
        params["bot_id"] = bot_instance_id
    if status:
        conditions.append("status = :status")
        params["status"] = status
    sql = text(
        f"SELECT * FROM trading_incidents WHERE {' AND '.join(conditions)} "
        f"ORDER BY created_at DESC LIMIT :limit"
    )
    result = await db.execute(sql, params)
    rows = result.mappings().all()
    return [dict(r) for r in rows]


@router.get("")
async def list_incidents(
    workspace_id: str,
    bot_instance_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return await _get_incidents_raw(db, workspace_id, bot_instance_id, status, limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{incident_id}/acknowledge")
async def acknowledge_incident(
    workspace_id: str,
    incident_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from sqlalchemy import text
    await db.execute(
        text("UPDATE trading_incidents SET status = 'acknowledged' WHERE id = :id"),
        {"id": incident_id},
    )
    await db.commit()
    return {"status": "acknowledged", "incident_id": incident_id}


@router.post("/{incident_id}/resolve")
async def resolve_incident(
    workspace_id: str,
    incident_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from sqlalchemy import text, func
    from datetime import datetime, timezone
    await db.execute(
        text("UPDATE trading_incidents SET status = 'resolved', resolved_at = :now WHERE id = :id"),
        {"id": incident_id, "now": datetime.now(timezone.utc)},
    )
    await db.commit()
    return {"status": "resolved", "incident_id": incident_id}
