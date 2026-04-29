from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.dependencies.auth import get_current_user
from app.models import User
from app.services.safety_ledger import SafetyLedgerService

router = APIRouter(prefix="/v1/workspaces/{workspace_id}/bots/{bot_id}", tags=["live-trading"])


def _get_registry(request: Request):
    return getattr(request.app.state, "registry", None)


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


@router.get("/order-state-transitions")
async def get_order_state_transitions(
    workspace_id: str,
    bot_id: str,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc = SafetyLedgerService(db)
    return await svc.list_order_state_transitions(bot_id, limit)


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
    return await svc.list_incidents(bot_id, limit)


@router.get("/reconciliation-runs")
async def get_reconciliation_runs(
    workspace_id: str,
    bot_id: str,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc = SafetyLedgerService(db)
    return await svc.list_reconciliation_runs(bot_id, limit)


@router.post("/reconcile-now")
async def reconcile_now(
    workspace_id: str,
    bot_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    registry = _get_registry(request)
    if registry is None:
        raise HTTPException(status_code=503, detail="Runtime registry unavailable")
    runtime = registry.get(bot_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="Runtime not found")
    try:
        if hasattr(runtime, "reconcile_now"):
            return await runtime.reconcile_now()
        worker = getattr(runtime, "_reconciliation_worker", None)
        if worker is None:
            raise RuntimeError("reconciliation_worker_not_running")
        result = await worker.run_once()
        return result.to_dict() if hasattr(result, "to_dict") else dict(result)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/incidents/{incident_id}/resolve")
async def resolve_incident(
    workspace_id: str,
    bot_id: str,
    incident_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc = SafetyLedgerService(db)
    row = await svc.resolve_incident(incident_id)
    if row is None or row.bot_instance_id != bot_id:
        raise HTTPException(status_code=404, detail="Incident not found")
    return {"status": row.status, "incident_id": row.id}


@router.post("/daily-state/reset-lock")
async def reset_daily_state_lock(
    workspace_id: str,
    bot_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc = SafetyLedgerService(db)
    state = await svc.reset_daily_lock(bot_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Daily state not found")
    registry = _get_registry(request)
    if registry is not None and registry.get(bot_id) is not None:
        runtime = registry.get(bot_id)
        state_obj = getattr(runtime, "state", None)
        metadata = getattr(state_obj, "metadata", {})
        if isinstance(metadata, dict):
            metadata["kill_switch"] = False
    return {
        "bot_instance_id": bot_id,
        "locked": bool(state.locked),
        "lock_reason": state.lock_reason,
    }


@router.post("/kill-switch")
async def set_kill_switch(
    workspace_id: str,
    bot_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    registry = _get_registry(request)
    runtime = registry.get(bot_id) if registry is not None else None
    if runtime is None:
        raise HTTPException(status_code=404, detail="Runtime not found")
    state_obj = getattr(runtime, "state", None)
    metadata = getattr(state_obj, "metadata", {})
    if not isinstance(metadata, dict):
        raise HTTPException(status_code=500, detail="Runtime metadata unavailable")
    metadata["kill_switch"] = True
    if state_obj is not None:
        state_obj.error_message = "kill_switch_enabled_by_operator"
    return {"bot_instance_id": bot_id, "kill_switch": True}


@router.post("/reset-kill-switch")
async def reset_kill_switch(
    workspace_id: str,
    bot_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    registry = _get_registry(request)
    runtime = registry.get(bot_id) if registry is not None else None
    if runtime is None:
        raise HTTPException(status_code=404, detail="Runtime not found")
    state_obj = getattr(runtime, "state", None)
    metadata = getattr(state_obj, "metadata", {})
    if not isinstance(metadata, dict):
        raise HTTPException(status_code=500, detail="Runtime metadata unavailable")
    metadata["kill_switch"] = False
    return {"bot_instance_id": bot_id, "kill_switch": False}
