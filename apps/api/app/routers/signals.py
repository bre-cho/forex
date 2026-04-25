"""Signals, orders, and trades routers."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.permissions import require_workspace_role
from app.models import BotInstance, Order, Signal, Trade, User
from app.schemas import OrderOut, SignalOut, TradeOut

signals_router = APIRouter(
    prefix="/v1/workspaces/{workspace_id}/bots/{bot_id}/signals",
    tags=["signals"],
)

orders_router = APIRouter(
    prefix="/v1/workspaces/{workspace_id}/bots/{bot_id}/orders",
    tags=["trades"],
)

trades_router = APIRouter(
    prefix="/v1/workspaces/{workspace_id}/bots/{bot_id}/trades",
    tags=["trades"],
)


@signals_router.get("", response_model=list[SignalOut])
async def list_signals(
    workspace_id: str,
    bot_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _member=Depends(require_workspace_role("viewer")),
):
    result = await db.execute(
        select(Signal)
        .join(BotInstance, BotInstance.id == Signal.bot_instance_id)
        .where(Signal.bot_instance_id == bot_id)
        .where(BotInstance.workspace_id == workspace_id)
        .order_by(Signal.created_at.desc())
        .limit(200)
    )
    return result.scalars().all()


@orders_router.get("", response_model=list[OrderOut])
async def list_orders(
    workspace_id: str,
    bot_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _member=Depends(require_workspace_role("viewer")),
):
    result = await db.execute(
        select(Order)
        .join(BotInstance, BotInstance.id == Order.bot_instance_id)
        .where(Order.bot_instance_id == bot_id)
        .where(BotInstance.workspace_id == workspace_id)
        .order_by(Order.created_at.desc())
        .limit(500)
    )
    return result.scalars().all()


@trades_router.get("", response_model=list[TradeOut])
async def list_trades(
    workspace_id: str,
    bot_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _member=Depends(require_workspace_role("viewer")),
):
    result = await db.execute(
        select(Trade)
        .join(BotInstance, BotInstance.id == Trade.bot_instance_id)
        .where(Trade.bot_instance_id == bot_id)
        .where(BotInstance.workspace_id == workspace_id)
        .order_by(Trade.opened_at.desc())
        .limit(500)
    )
    return result.scalars().all()
