"""Admin router — superuser endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.dependencies.auth import get_current_user
from app.models import BotInstance, Subscription, User, Workspace

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
