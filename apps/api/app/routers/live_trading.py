from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.dependencies.auth import get_current_user
from app.models import User
from app.services.safety_ledger import SafetyLedgerService

router = APIRouter(prefix="/v1/workspaces/{workspace_id}/bots/{bot_id}", tags=["live-trading"])


@router.get("/timeline")
async def get_timeline(
    workspace_id: str,
    bot_id: str,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc = SafetyLedgerService(db)
    return await svc.timeline(bot_id, limit)


@router.get("/decision-ledger")
async def get_decisions(
    workspace_id: str,
    bot_id: str,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc = SafetyLedgerService(db)
    timeline = await svc.timeline(bot_id, limit)
    return timeline["decisions"]


@router.get("/gate-events")
async def get_gate_events(
    workspace_id: str,
    bot_id: str,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc = SafetyLedgerService(db)
    timeline = await svc.timeline(bot_id, limit)
    return timeline["gate_events"]


@router.get("/daily-state")
async def get_daily_state(
    workspace_id: str,
    bot_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc = SafetyLedgerService(db)
    row = await svc.get_daily_state(bot_id)
    if row is None:
        return {
            "bot_instance_id": bot_id,
            "locked": False,
            "daily_profit_amount": 0.0,
            "daily_loss_pct": 0.0,
            "consecutive_losses": 0,
            "trades_count": 0,
            "trading_day": None,
        }
    return row


@router.get("/incidents")
async def get_incidents(
    workspace_id: str,
    bot_id: str,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc = SafetyLedgerService(db)
    timeline = await svc.timeline(bot_id, limit)
    return timeline["incidents"]
