"""Bots router — CRUD + lifecycle (start/stop/pause/resume) + runtime."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.permissions import require_workspace_role
from app.models import BotInstance, BotInstanceConfig, BotRuntimeSnapshot, BrokerConnection, Strategy, User
from app.schemas import (
    BotConfigUpdate,
    BotCreate,
    BotOut,
    BotRuntimeSnapshotOut,
    BotUpdate,
)

router = APIRouter(prefix="/v1/workspaces/{workspace_id}/bots", tags=["bots"])


def _get_registry(request: Request):
    return getattr(request.app.state, "registry", None)


async def _get_bot_or_404(bot_id: str, workspace_id: str, db: AsyncSession) -> BotInstance:
    result = await db.execute(
        select(BotInstance).where(
            BotInstance.id == bot_id,
            BotInstance.workspace_id == workspace_id,
        )
    )
    bot = result.scalar_one_or_none()
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")
    return bot


async def _assert_same_workspace(
    workspace_id: str,
    strategy_id: str | None,
    broker_connection_id: str | None,
    db: AsyncSession,
) -> None:
    """Raise 400 if referenced strategy or broker connection belongs to a different workspace."""
    if strategy_id:
        result = await db.execute(select(Strategy).where(Strategy.id == strategy_id))
        s = result.scalar_one_or_none()
        if s is None:
            raise HTTPException(status_code=404, detail="Strategy not found")
        if s.workspace_id != workspace_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Strategy does not belong to this workspace",
            )
    if broker_connection_id:
        result = await db.execute(
            select(BrokerConnection).where(BrokerConnection.id == broker_connection_id)
        )
        bc = result.scalar_one_or_none()
        if bc is None:
            raise HTTPException(status_code=404, detail="Broker connection not found")
        if bc.workspace_id != workspace_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Broker connection does not belong to this workspace",
            )


@router.post("", response_model=BotOut, status_code=status.HTTP_201_CREATED)
async def create_bot(
    workspace_id: str,
    body: BotCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _member=Depends(require_workspace_role("trader")),
):
    await _assert_same_workspace(workspace_id, body.strategy_id, body.broker_connection_id, db)
    bot = BotInstance(
        workspace_id=workspace_id,
        name=body.name,
        symbol=body.symbol,
        timeframe=body.timeframe,
        mode=body.mode,
        strategy_id=body.strategy_id,
        broker_connection_id=body.broker_connection_id,
    )
    db.add(bot)
    await db.flush()
    config = BotInstanceConfig(bot_instance_id=bot.id)
    db.add(config)
    return bot


@router.get("", response_model=list[BotOut])
async def list_bots(
    workspace_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _member=Depends(require_workspace_role("viewer")),
):
    result = await db.execute(
        select(BotInstance).where(BotInstance.workspace_id == workspace_id)
    )
    return result.scalars().all()


@router.get("/{bot_id}", response_model=BotOut)
async def get_bot(
    workspace_id: str,
    bot_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _member=Depends(require_workspace_role("viewer")),
):
    return await _get_bot_or_404(bot_id, workspace_id, db)


@router.patch("/{bot_id}", response_model=BotOut)
async def update_bot(
    workspace_id: str,
    bot_id: str,
    body: BotUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _member=Depends(require_workspace_role("trader")),
):
    bot = await _get_bot_or_404(bot_id, workspace_id, db)
    if body.broker_connection_id is not None:
        await _assert_same_workspace(workspace_id, None, body.broker_connection_id, db)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(bot, field, value)
    return bot


@router.delete("/{bot_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_bot(
    workspace_id: str,
    bot_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _member=Depends(require_workspace_role("admin")),
):
    bot = await _get_bot_or_404(bot_id, workspace_id, db)
    registry = _get_registry(request)
    if registry and registry.get(bot_id):
        await registry.remove(bot_id)
    await db.delete(bot)


@router.post("/{bot_id}/start")
async def start_bot(
    workspace_id: str,
    bot_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _member=Depends(require_workspace_role("trader")),
):
    bot = await _get_bot_or_404(bot_id, workspace_id, db)
    registry = _get_registry(request)
    if registry is None:
        raise HTTPException(status_code=503, detail="Runtime registry unavailable")
    if registry.get(bot_id) is None:
        from app.services.bot_service import create_runtime_for_bot
        await create_runtime_for_bot(bot, registry, db)
    await registry.start(bot_id)
    bot.status = "running"
    return {"status": "running", "bot_id": bot_id}


@router.post("/{bot_id}/stop")
async def stop_bot(
    workspace_id: str,
    bot_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _member=Depends(require_workspace_role("trader")),
):
    bot = await _get_bot_or_404(bot_id, workspace_id, db)
    registry = _get_registry(request)
    if registry and registry.get(bot_id):
        await registry.stop(bot_id)
    bot.status = "stopped"
    return {"status": "stopped", "bot_id": bot_id}


@router.post("/{bot_id}/pause")
async def pause_bot(
    workspace_id: str,
    bot_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _member=Depends(require_workspace_role("trader")),
):
    bot = await _get_bot_or_404(bot_id, workspace_id, db)
    registry = _get_registry(request)
    if registry and registry.get(bot_id):
        await registry.pause(bot_id)
    bot.status = "paused"
    return {"status": "paused", "bot_id": bot_id}


@router.post("/{bot_id}/resume")
async def resume_bot(
    workspace_id: str,
    bot_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _member=Depends(require_workspace_role("trader")),
):
    bot = await _get_bot_or_404(bot_id, workspace_id, db)
    registry = _get_registry(request)
    if registry and registry.get(bot_id):
        await registry.resume(bot_id)
    bot.status = "running"
    return {"status": "running", "bot_id": bot_id}


@router.get("/{bot_id}/runtime")
async def get_runtime(
    workspace_id: str,
    bot_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _member=Depends(require_workspace_role("viewer")),
):
    await _get_bot_or_404(bot_id, workspace_id, db)
    registry = _get_registry(request)
    if registry is None or registry.get(bot_id) is None:
        return {"status": "not_running", "bot_id": bot_id}
    return await registry.get_snapshot(bot_id)


@router.get("/{bot_id}/snapshots", response_model=list[BotRuntimeSnapshotOut])
async def list_snapshots(
    workspace_id: str,
    bot_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _member=Depends(require_workspace_role("viewer")),
):
    await _get_bot_or_404(bot_id, workspace_id, db)
    result = await db.execute(
        select(BotRuntimeSnapshot)
        .where(BotRuntimeSnapshot.bot_instance_id == bot_id)
        .order_by(BotRuntimeSnapshot.recorded_at.desc())
        .limit(100)
    )
    return result.scalars().all()


@router.patch("/{bot_id}/config")
async def update_bot_config(
    workspace_id: str,
    bot_id: str,
    body: BotConfigUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _member=Depends(require_workspace_role("trader")),
):
    await _get_bot_or_404(bot_id, workspace_id, db)
    result = await db.execute(
        select(BotInstanceConfig).where(BotInstanceConfig.bot_instance_id == bot_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Config not found")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(config, field, value)
    return {"message": "Config updated"}

