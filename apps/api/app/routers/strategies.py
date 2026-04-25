"""Strategies router."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.permissions import require_workspace_role
from app.models import Strategy, StrategyVersion, User
from app.schemas import (
    StrategyCreate,
    StrategyOut,
    StrategyUpdate,
    StrategyVersionOut,
)

router = APIRouter(
    prefix="/v1/workspaces/{workspace_id}/strategies",
    tags=["strategies"],
)


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
    for field, value in body.model_dump(exclude_none=True).items():
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

