"""Admin router — superuser endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.dependencies.auth import get_current_user
from app.models import BotInstance, Subscription, User, Workspace
from app.services.disaster_recovery_service import DisasterRecoveryService

router = APIRouter(prefix="/v1/admin", tags=["admin"])


def _require_admin(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


@router.get("/stats")
async def admin_stats(
    admin: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    user_count = (await db.execute(select(func.count()).select_from(User))).scalar()
    workspace_count = (await db.execute(select(func.count()).select_from(Workspace))).scalar()
    bot_count = (await db.execute(select(func.count()).select_from(BotInstance))).scalar()
    sub_count = (await db.execute(select(func.count()).select_from(Subscription))).scalar()
    return {
        "users": user_count,
        "workspaces": workspace_count,
        "bots": bot_count,
        "subscriptions": sub_count,
    }


@router.get("/users")
async def admin_users(
    admin: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).limit(200))
    return [
        {"id": u.id, "email": u.email, "is_active": u.is_active, "created_at": u.created_at}
        for u in result.scalars().all()
    ]


@router.get("/runtime")
async def admin_runtime(admin: User = Depends(_require_admin)):
    """Return list of all active runtimes (in-process)."""
    from app.core.registry import get_registry
    registry = get_registry()
    if registry is None:
        return {"runtimes": []}
    return {"runtimes": registry.list_all()}


@router.get("/health/live-hard")
async def health_live_hard(
    admin: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Hard liveness check — verifies DB reachability, registry, and reconciliation daemon.

    Returns 200 {"status": "ok", ...} if all subsystems are healthy.
    Returns 503 {"status": "degraded", ...} if any subsystem is unhealthy.

    Intended for infra/load-balancer health checks (authenticated admin-only).
    """
    import time
    from fastapi.responses import JSONResponse

    checks: dict[str, str] = {}

    # 1. DB round-trip
    try:
        await db.execute(select(func.count()).select_from(User))
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = f"error: {exc}"

    # 2. In-process registry
    try:
        from app.core.registry import get_registry
        registry = get_registry()
        if registry is not None:
            checks["registry"] = "ok"
        else:
            checks["registry"] = "not_initialised"
    except Exception as exc:
        checks["registry"] = f"error: {exc}"

    # 3. Reconciliation daemon alive flag (daemon sets this attribute on the module)
    try:
        from app.workers import reconciliation_daemon as _rd
        daemon_alive = bool(getattr(_rd, "_daemon_running", False))
        checks["reconciliation_daemon"] = "ok" if daemon_alive else "not_running"
    except Exception as exc:
        checks["reconciliation_daemon"] = f"error: {exc}"

    # 4. Submit outbox recovery worker alive flag
    try:
        from app.workers import submit_outbox_recovery_worker as _sow
        outbox_worker_alive = bool(getattr(_sow, "_worker_running", False))
        checks["submit_outbox_recovery_worker"] = "ok" if outbox_worker_alive else "not_running"
    except Exception as exc:
        checks["submit_outbox_recovery_worker"] = f"error: {exc}"

    all_ok = all(v == "ok" for v in checks.values())
    payload = {
        "status": "ok" if all_ok else "degraded",
        "checks": checks,
        "ts": time.time(),
    }
    status_code = 200 if all_ok else 503
    return JSONResponse(content=payload, status_code=status_code)


@router.get("/dr/snapshots")
async def list_dr_snapshots(
    limit: int = 50,
    admin: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    svc = DisasterRecoveryService(db)
    return {"snapshots": svc.list_snapshots(limit=limit)}


@router.post("/dr/snapshot")
async def create_dr_snapshot(
    payload: dict | None = None,
    admin: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    body = payload or {}
    include_runtime = bool(body.get("include_runtime", True))
    svc = DisasterRecoveryService(db)
    result = await svc.create_snapshot(include_runtime=include_runtime)
    return {"status": "created", **result}


@router.post("/dr/restore/{snapshot_id}")
async def restore_dr_snapshot(
    snapshot_id: str,
    payload: dict | None = None,
    admin: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    body = payload or {}
    svc = DisasterRecoveryService(db)
    try:
        result = await svc.restore_snapshot(
            snapshot_id=snapshot_id,
            dry_run=bool(body.get("dry_run", True)),
            restore_workspace_pause_flag=bool(body.get("restore_workspace_pause_flag", True)),
            stop_non_running_runtimes=bool(body.get("stop_non_running_runtimes", True)),
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="snapshot_not_found")
    return {"status": "ok", **result}
