"""Public router — unauthenticated endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.models import BotInstance, Strategy, Trade
from app.schemas import StrategyOut

router = APIRouter(prefix="/v1/public", tags=["public"])


@router.get("/strategies", response_model=list[StrategyOut])
async def public_strategies(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Strategy).where(Strategy.is_public.is_(True)).limit(50))
    return result.scalars().all()


@router.get("/performance/leaderboard")
async def leaderboard(db: AsyncSession = Depends(get_db)):
    """Return the top 20 bots ranked by total realised PnL (public bots only).

    A bot is eligible when its strategy is public.  The leaderboard aggregates
    closed trades and returns summary statistics per bot.
    """
    # Aggregate closed trades per bot that is linked to a public strategy
    rows = await db.execute(
        select(
            BotInstance.id.label("bot_id"),
            BotInstance.name.label("bot_name"),
            BotInstance.symbol,
            BotInstance.timeframe,
            BotInstance.mode,
            func.count(Trade.id).label("total_trades"),
            func.coalesce(func.sum(Trade.pnl), 0).label("total_pnl"),
            func.coalesce(
                func.sum(
                    # win count: positive pnl
                    func.cast(Trade.pnl > 0, type_=func.Float())  # type: ignore[arg-type]
                ),
                0,
            ).label("win_count"),
        )
        .join(Trade, Trade.bot_instance_id == BotInstance.id, isouter=True)
        .join(Strategy, Strategy.id == BotInstance.strategy_id)
        .where(
            Strategy.is_public.is_(True),
            Trade.pnl.isnot(None),
        )
        .group_by(
            BotInstance.id,
            BotInstance.name,
            BotInstance.symbol,
            BotInstance.timeframe,
            BotInstance.mode,
        )
        .order_by(func.coalesce(func.sum(Trade.pnl), 0).desc())
        .limit(20)
    )

    items = []
    for row in rows.mappings():
        total_trades = row["total_trades"] or 0
        win_count = row["win_count"] or 0
        win_rate = (win_count / total_trades) if total_trades > 0 else 0.0
        items.append(
            {
                "bot_id": row["bot_id"],
                "bot_name": row["bot_name"],
                "symbol": row["symbol"],
                "timeframe": row["timeframe"],
                "mode": row["mode"],
                "total_trades": total_trades,
                "total_pnl": round(float(row["total_pnl"]), 2),
                "win_rate": round(win_rate, 4),
            }
        )

    return {"items": items, "count": len(items)}

