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


def _lifecycle_response(target_status: str, bot_id: str, already_in_state: bool) -> dict:
    return {
        "status": target_status,
        "bot_id": bot_id,
        "idempotent": True,
        "already_in_state": already_in_state,
    }


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


async def _validate_live_mode_requirements(
    workspace_id: str,
    mode: str,
    broker_connection_id: str | None,
    db: AsyncSession,
) -> None:
    if mode != "live":
        return
    if not broker_connection_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Live mode requires broker_connection_id",
        )
    result = await db.execute(
        select(BrokerConnection).where(
            BrokerConnection.id == broker_connection_id,
            BrokerConnection.workspace_id == workspace_id,
        )
    )
    conn = result.scalar_one_or_none()
    if conn is None:
        raise HTTPException(status_code=404, detail="Broker connection not found")
    if not conn.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Live mode requires an active broker connection",
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
    await _validate_live_mode_requirements(workspace_id, body.mode, body.broker_connection_id, db)
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
    if body.strategy_id is not None:
        await _assert_same_workspace(workspace_id, body.strategy_id, None, db)
    if body.broker_connection_id is not None:
        await _assert_same_workspace(workspace_id, None, body.broker_connection_id, db)
    effective_mode = body.mode or bot.mode
    effective_broker_connection_id = (
        body.broker_connection_id
        if body.broker_connection_id is not None
        else bot.broker_connection_id
    )
    await _validate_live_mode_requirements(
        workspace_id,
        effective_mode,
        effective_broker_connection_id,
        db,
    )
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
    await _validate_live_mode_requirements(workspace_id, bot.mode, bot.broker_connection_id, db)
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
    already_running = bot.status == "running"
    if registry.get(bot_id) is None:
        from app.services.bot_service import create_runtime_for_bot
        await create_runtime_for_bot(bot, registry, db)
    try:
        await registry.start(bot_id)
        if bot.mode == "live":
            from app.services.bot_service import assert_runtime_live_guard

            await assert_runtime_live_guard(bot, registry)
    except RuntimeError as exc:
        bot.status = "error"
        raise HTTPException(status_code=503, detail=str(exc))
    bot.status = "running"
    return _lifecycle_response("running", bot_id, already_running)


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
    already_stopped = bot.status == "stopped"
    if registry and registry.get(bot_id):
        await registry.stop(bot_id)
    bot.status = "stopped"
    return _lifecycle_response("stopped", bot_id, already_stopped)


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
    already_paused = bot.status == "paused"
    if registry and registry.get(bot_id):
        await registry.pause(bot_id)
    bot.status = "paused"
    return _lifecycle_response("paused", bot_id, already_paused)


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
    already_running = bot.status == "running"
    if registry and registry.get(bot_id):
        await registry.resume(bot_id)
    bot.status = "running"
    return _lifecycle_response("running", bot_id, already_running)


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


@router.post("/{bot_id}/tick")
async def trigger_tick(
    workspace_id: str,
    bot_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _member=Depends(require_workspace_role("trader")),
):
    bot = await _get_bot_or_404(bot_id, workspace_id, db)
    registry = _get_registry(request)
    if registry is None or registry.get(bot_id) is None:
        raise HTTPException(status_code=404, detail="Runtime not found")
    runtime = registry.get(bot_id)
    await runtime.tick()
    if bot.mode == "live":
        from app.services.bot_service import assert_runtime_live_guard

        try:
            await assert_runtime_live_guard(bot, registry)
        except RuntimeError as exc:
            bot.status = "error"
            raise HTTPException(status_code=503, detail=str(exc))
    return await runtime.get_snapshot()


@router.get("/{bot_id}/readiness")
async def get_readiness(
    workspace_id: str,
    bot_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _member=Depends(require_workspace_role("viewer")),
):
    bot = await _get_bot_or_404(bot_id, workspace_id, db)
    registry = _get_registry(request)
    from app.services.bot_service import get_runtime_readiness

    return await get_runtime_readiness(bot, registry)


@router.post("/{bot_id}/positions/{position_id}/close")
async def close_position(
    workspace_id: str,
    bot_id: str,
    position_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _member=Depends(require_workspace_role("trader")),
):
    await _get_bot_or_404(bot_id, workspace_id, db)
    registry = _get_registry(request)
    if registry is None or registry.get(bot_id) is None:
        raise HTTPException(status_code=404, detail="Runtime not found")
    runtime = registry.get(bot_id)
    try:
        payload = await runtime.close_position(position_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return payload


@router.post("/{bot_id}/manual-signal")
async def submit_manual_signal(
    workspace_id: str,
    bot_id: str,
    request: Request,
    direction: str = "BUY",
    confidence: float = 0.95,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _member=Depends(require_workspace_role("trader")),
):
    await _get_bot_or_404(bot_id, workspace_id, db)
    registry = _get_registry(request)
    if registry is None or registry.get(bot_id) is None:
        raise HTTPException(status_code=404, detail="Runtime not found")
    runtime = registry.get(bot_id)
    try:
        signal = await runtime.submit_manual_signal(direction=direction, confidence=confidence)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "queued", "signal": signal}


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
