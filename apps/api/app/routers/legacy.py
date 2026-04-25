"""
Legacy router — maps all existing /api/* endpoints to the new v1 system.
Preserves 100% backward compatibility with the original backend/main.py API.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.dependencies.auth import get_current_user, get_optional_user
from app.models import BotInstance, Signal, Trade, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["legacy"])


# ── Bot status (legacy) ────────────────────────────────────────────────────

@router.get("/bot/status")
async def legacy_bot_status(request: Request):
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        return {"status": "not_running", "running": False}
    runtimes = registry.list_all()
    if not runtimes:
        return {"status": "stopped", "running": False}
    first = runtimes[0]
    return {
        "status": first.get("status", "unknown"),
        "running": first.get("status") == "running",
        "bot_count": len(runtimes),
    }


@router.post("/bot/start")
async def legacy_bot_start(request: Request):
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="Runtime registry unavailable")
    runtimes = registry.list_all()
    if runtimes:
        bot_id = runtimes[0]["bot_instance_id"]
        await registry.start(bot_id)
        return {"message": "Bot started", "bot_id": bot_id}
    return {"message": "No bots configured"}


@router.post("/bot/stop")
async def legacy_bot_stop(request: Request):
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="Runtime registry unavailable")
    await registry.stop_all()
    return {"message": "All bots stopped"}


@router.get("/trades")
async def legacy_trades(
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Trade).order_by(Trade.opened_at.desc()).limit(limit)
    )
    trades = result.scalars().all()
    return [
        {
            "id": t.id,
            "symbol": t.symbol,
            "side": t.side,
            "volume": t.volume,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "pnl": t.pnl,
            "opened_at": t.opened_at.isoformat(),
        }
        for t in trades
    ]


@router.get("/signals")
async def legacy_signals(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Signal).order_by(Signal.created_at.desc()).limit(limit)
    )
    signals = result.scalars().all()
    return [
        {
            "id": s.id,
            "symbol": s.symbol,
            "direction": s.direction,
            "confidence": s.confidence,
            "wave_state": s.wave_state,
            "entry_price": s.entry_price,
            "stop_loss": s.stop_loss,
            "take_profit": s.take_profit,
            "created_at": s.created_at.isoformat(),
        }
        for s in signals
    ]


@router.get("/health")
async def legacy_health():
    return {"status": "ok", "service": "forex-api"}


@router.get("/config")
async def legacy_config():
    from app.core.config import get_settings
    s = get_settings()
    return {
        "symbol": s.ctrader_symbol,
        "timeframe": s.ctrader_timeframe,
        "mode": "live" if s.ctrader_live else "demo",
        "llm_provider": s.llm_provider,
    }
