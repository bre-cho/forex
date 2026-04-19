"""Analytics router."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.permissions import require_workspace_role
from app.models import BotInstance, Trade, User

router = APIRouter(
    prefix="/v1/workspaces/{workspace_id}/analytics",
    tags=["analytics"],
)


@router.get("/summary")
async def analytics_summary(
    workspace_id: str,
    bot_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _member=Depends(require_workspace_role("viewer")),
):
    """Return aggregated performance metrics for a workspace or specific bot."""
    query = select(Trade).join(BotInstance, BotInstance.id == Trade.bot_instance_id).where(
        BotInstance.workspace_id == workspace_id
    )
    if bot_id:
        query = query.where(Trade.bot_instance_id == bot_id)
    result = await db.execute(query)
    trades = result.scalars().all()

    pnl_list = [t.pnl for t in trades if t.pnl is not None]
    total_pnl = sum(pnl_list)
    wins = [p for p in pnl_list if p > 0]
    losses = [p for p in pnl_list if p < 0]   # break-even trades (pnl == 0) excluded from both

    win_rate = len(wins) / len(pnl_list) if pnl_list else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    profit_factor = (
        sum(wins) / abs(sum(losses)) if losses else float("inf") if wins else 0.0
    )

    return {
        "total_trades": len(trades),
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
    }


@router.get("/equity-curve")
async def equity_curve(
    workspace_id: str,
    bot_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _member=Depends(require_workspace_role("viewer")),
):
    """Return equity curve data points."""
    query = (
        select(Trade)
        .join(BotInstance, BotInstance.id == Trade.bot_instance_id)
        .where(BotInstance.workspace_id == workspace_id)
        .order_by(Trade.opened_at)
    )
    if bot_id:
        query = query.where(Trade.bot_instance_id == bot_id)
    result = await db.execute(query)
    trades = result.scalars().all()

    balance = 10000.0
    curve = []
    for t in trades:
        if t.pnl is not None:
            balance += t.pnl
        curve.append({
            "timestamp": t.closed_at.isoformat() if t.closed_at else t.opened_at.isoformat(),
            "equity": balance,
            "trade_id": t.id,
        })
    return curve
