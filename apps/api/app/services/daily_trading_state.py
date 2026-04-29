from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailyTradingState


class DailyTradingStateService:
    """Loads and updates per-bot daily state used by pre-execution safety gate."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_or_create(self, bot_instance_id: str, trading_day: Optional[date] = None) -> DailyTradingState:
        trading_day = trading_day or date.today()
        result = await self.db.execute(
            select(DailyTradingState).where(
                DailyTradingState.bot_instance_id == bot_instance_id,
                DailyTradingState.trading_day == trading_day,
            )
        )
        state = result.scalar_one_or_none()
        if state is not None:
            return state
        state = DailyTradingState(
            bot_instance_id=bot_instance_id,
            trading_day=trading_day,
            starting_equity=0.0,
            current_equity=0.0,
            daily_profit_amount=0.0,
            daily_loss_pct=0.0,
            trades_count=0,
            consecutive_losses=0,
            locked=False,
        )
        self.db.add(state)
        await self.db.commit()
        await self.db.refresh(state)
        return state

    async def update_after_trade(
        self,
        bot_instance_id: str,
        equity: float,
        pnl: float,
    ) -> DailyTradingState:
        state = await self.recompute_from_broker_equity(bot_instance_id, equity)
        state.trades_count = int(state.trades_count or 0) + 1
        if pnl < 0:
            state.consecutive_losses = int(state.consecutive_losses or 0) + 1
        else:
            state.consecutive_losses = 0
        state.updated_at = datetime.now(timezone.utc)
        await self.db.commit()
        await self.db.refresh(state)
        return state

    async def recompute_from_broker_equity(
        self,
        bot_instance_id: str,
        equity: float,
        trading_day: Optional[date] = None,
    ) -> DailyTradingState:
        state = await self.get_or_create(bot_instance_id, trading_day=trading_day)
        equity_value = float(equity or 0.0)
        if state.starting_equity is None or float(state.starting_equity) <= 0:
            state.starting_equity = equity_value
        state.current_equity = equity_value
        state.daily_profit_amount = float((state.current_equity or 0.0) - (state.starting_equity or 0.0))
        if float(state.starting_equity or 0.0) > 0:
            state.daily_loss_pct = max(0.0, -state.daily_profit_amount / float(state.starting_equity) * 100.0)
        else:
            state.daily_loss_pct = 0.0
        state.updated_at = datetime.now(timezone.utc)
        await self.db.flush()
        return state

    async def lock_day(self, bot_instance_id: str, reason: str) -> DailyTradingState:
        state = await self.get_or_create(bot_instance_id)
        state.locked = True
        state.lock_reason = reason
        state.updated_at = datetime.now(timezone.utc)
        await self.db.commit()
        await self.db.refresh(state)
        return state
