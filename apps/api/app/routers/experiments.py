from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.dependencies.auth import get_current_user
from app.models import User
from app.services.experiment_registry_service import ExperimentRegistryService

router = APIRouter(
    prefix="/v1/workspaces/{workspace_id}/bots/{bot_id}/experiments",
    tags=["experiments"],
)


@router.get("")
async def list_experiments(
    workspace_id: str,
    bot_id: str,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc = ExperimentRegistryService(db)
    return await svc.list_experiments(bot_id, limit)


@router.post("")
async def create_experiment(
    workspace_id: str,
    bot_id: str,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    strategy_snapshot = payload.get("strategy_snapshot")
    policy_snapshot = payload.get("policy_snapshot")
    if not isinstance(strategy_snapshot, dict) or not isinstance(policy_snapshot, dict):
        raise HTTPException(status_code=400, detail="strategy_snapshot and policy_snapshot must be objects")

    svc = ExperimentRegistryService(db)
    row = await svc.create_experiment(
        bot_instance_id=bot_id,
        strategy_snapshot=strategy_snapshot,
        policy_snapshot=policy_snapshot,
        note=str(payload.get("note") or "") or None,
        created_by=str(getattr(current_user, "id", "") or "") or None,
    )
    return {"status": "created", "experiment": row}


@router.post("/{version}/advance")
async def advance_experiment_stage(
    workspace_id: str,
    bot_id: str,
    version: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    stage = str(payload.get("stage") or "").upper()
    if not stage:
        raise HTTPException(status_code=400, detail="stage is required")

    svc = ExperimentRegistryService(db)
    try:
        row = await svc.advance_stage(
            bot_instance_id=bot_id,
            version=version,
            stage=stage,
            metrics_snapshot=payload.get("metrics_snapshot") if isinstance(payload.get("metrics_snapshot"), dict) else None,
            note=str(payload.get("note") or "") or None,
            actor_user_id=str(getattr(current_user, "id", "") or "") or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if row is None:
        raise HTTPException(status_code=404, detail="experiment version not found")
    return {"status": "advanced", "experiment": row}
