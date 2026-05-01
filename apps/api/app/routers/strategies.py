"""Strategies router."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.permissions import require_workspace_role
from app.models import BotInstance, Strategy, StrategyVersion, User
from app.schemas import (
    StrategyCreate,
    StrategyOut,
    StrategyUpdate,
    StrategyVersionOut,
)
from app.services.action_approval_service import ActionApprovalService

router = APIRouter(
    prefix="/v1/workspaces/{workspace_id}/strategies",
    tags=["strategies"],
)

_STAGE_ORDER = ["DRAFT", "PAPER_TEST", "DEMO_TEST", "LIVE_APPROVED", "RETIRED"]


async def _next_strategy_version(db: AsyncSession, strategy_id: str) -> int:
    current_max = (
        await db.execute(
            select(func.max(StrategyVersion.version)).where(StrategyVersion.strategy_id == strategy_id)
        )
    ).scalar_one_or_none()
    return int(current_max or 0) + 1


@router.post("", response_model=StrategyOut, status_code=status.HTTP_201_CREATED)
async def create_strategy(
    workspace_id: str,
    body: StrategyCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _member=Depends(require_workspace_role("trader")),
):
    strategy = Strategy(
        workspace_id=workspace_id,
        name=body.name,
        description=body.description,
        is_public=body.is_public,
        config=body.config,
    )
    db.add(strategy)
    await db.flush()
    v1 = StrategyVersion(
        strategy_id=strategy.id,
        version=1,
        config_snapshot=body.config,
        change_notes="Initial version",
        stage="PAPER_TEST",
    )
    db.add(v1)
    return strategy


@router.get("", response_model=list[StrategyOut])
async def list_strategies(
    workspace_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _member=Depends(require_workspace_role("viewer")),
):
    result = await db.execute(
        select(Strategy).where(Strategy.workspace_id == workspace_id)
    )
    return result.scalars().all()


@router.get("/{strategy_id}", response_model=StrategyOut)
async def get_strategy(
    workspace_id: str,
    strategy_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _member=Depends(require_workspace_role("viewer")),
):
    result = await db.execute(
        select(Strategy).where(
            Strategy.id == strategy_id,
            Strategy.workspace_id == workspace_id,
        )
    )
    s = result.scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return s


@router.patch("/{strategy_id}", response_model=StrategyOut)
async def update_strategy(
    workspace_id: str,
    strategy_id: str,
    body: StrategyUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _member=Depends(require_workspace_role("trader")),
):
    result = await db.execute(
        select(Strategy).where(
            Strategy.id == strategy_id,
            Strategy.workspace_id == workspace_id,
        )
    )
    s = result.scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Strategy not found")
    updates = body.model_dump(exclude_none=True)

    if "config" in updates:
        has_live_bot = (
            (
                await db.execute(
                    select(BotInstance.id)
                    .where(
                        BotInstance.workspace_id == workspace_id,
                        BotInstance.strategy_id == strategy_id,
                        BotInstance.mode == "live",
                    )
                    .limit(1)
                )
            )
            .scalar_one_or_none()
        )
        if has_live_bot is not None:
            raise HTTPException(
                status_code=409,
                detail="live_strategy_mutation_blocked_create_new_version",
            )
        version = await _next_strategy_version(db, strategy_id)
        db.add(
            StrategyVersion(
                strategy_id=strategy_id,
                version=version,
                config_snapshot=dict(updates["config"] or {}),
                change_notes="Config update",
                stage="DRAFT",
            )
        )

    for field, value in updates.items():
        setattr(s, field, value)
    return s


@router.delete("/{strategy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_strategy(
    workspace_id: str,
    strategy_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _member=Depends(require_workspace_role("admin")),
):
    result = await db.execute(
        select(Strategy).where(
            Strategy.id == strategy_id,
            Strategy.workspace_id == workspace_id,
        )
    )
    s = result.scalar_one_or_none()
    if s:
        await db.delete(s)


@router.get("/{strategy_id}/versions", response_model=list[StrategyVersionOut])
async def list_versions(
    workspace_id: str,
    strategy_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _member=Depends(require_workspace_role("viewer")),
):
    result = await db.execute(
        select(StrategyVersion)
        .where(StrategyVersion.strategy_id == strategy_id)
        .order_by(StrategyVersion.version.desc())
    )
    return result.scalars().all()


@router.post("/{strategy_id}/versions", response_model=StrategyVersionOut)
async def create_version(
    workspace_id: str,
    strategy_id: str,
    payload: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _member=Depends(require_workspace_role("trader")),
):
    result = await db.execute(
        select(Strategy).where(
            Strategy.id == strategy_id,
            Strategy.workspace_id == workspace_id,
        )
    )
    s = result.scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Strategy not found")

    snapshot = payload.get("config_snapshot")
    if snapshot is None:
        snapshot = dict(getattr(s, "config", {}) or {})
    if not isinstance(snapshot, dict):
        raise HTTPException(status_code=400, detail="config_snapshot must be object")

    version = await _next_strategy_version(db, strategy_id)
    row = StrategyVersion(
        strategy_id=strategy_id,
        version=version,
        config_snapshot=dict(snapshot),
        change_notes=str(payload.get("change_notes") or "").strip(),
        stage="DRAFT",
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row


@router.post("/{strategy_id}/versions/{version}/promote", response_model=StrategyVersionOut)
async def promote_version_stage(
    workspace_id: str,
    strategy_id: str,
    version: int,
    payload: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _member=Depends(require_workspace_role("trader")),
):
    stage = str(payload.get("stage") or "").upper().strip()
    if stage not in _STAGE_ORDER:
        raise HTTPException(status_code=400, detail="invalid_stage")

    result = await db.execute(
        select(StrategyVersion)
        .join(Strategy, Strategy.id == StrategyVersion.strategy_id)
        .where(
            StrategyVersion.strategy_id == strategy_id,
            StrategyVersion.version == version,
            Strategy.workspace_id == workspace_id,
        )
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Strategy version not found")

    current_stage = str(getattr(row, "stage", "DRAFT") or "DRAFT").upper()
    if _STAGE_ORDER.index(stage) < _STAGE_ORDER.index(current_stage):
        raise HTTPException(status_code=400, detail=f"stage_regression_not_allowed:{current_stage}->{stage}")

    if stage == "LIVE_APPROVED":
        approval_id_raw = (payload or {}).get("approval_id")
        try:
            approval_id = int(approval_id_raw) if approval_id_raw is not None else None
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="invalid_approval_id")
        approval_svc = ActionApprovalService(db)
        await approval_svc.validate_and_consume_approval(
            approval_id=approval_id,
            workspace_id=workspace_id,
            action_type="promote_strategy_live",
            bot_instance_id=None,
            actor_user_id=str(getattr(current_user, "id", "") or "") or None,
        )
        row.approved_by = str(getattr(current_user, "id", "") or "") or None
        row.approved_at = datetime.now(timezone.utc)

    row.stage = stage
    await db.flush()
    await db.refresh(row)
    return row


@router.post("/{strategy_id}/publish")
async def publish_strategy(
    workspace_id: str,
    strategy_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _member=Depends(require_workspace_role("admin")),
):
    result = await db.execute(
        select(Strategy).where(
            Strategy.id == strategy_id,
            Strategy.workspace_id == workspace_id,
        )
    )
    s = result.scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Strategy not found")
    s.is_public = True
    return {"message": "Strategy published"}

