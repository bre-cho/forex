"""Public router — unauthenticated endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.models import Strategy, Trade
from app.schemas import StrategyOut

router = APIRouter(prefix="/v1/public", tags=["public"])


@router.get("/strategies", response_model=list[StrategyOut])
async def public_strategies(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Strategy).where(Strategy.is_public == True).limit(50))
    return result.scalars().all()


@router.get("/performance/leaderboard")
async def leaderboard(db: AsyncSession = Depends(get_db)):
    """Return top performing bots (public)."""
    return {"message": "Leaderboard coming soon", "items": []}
