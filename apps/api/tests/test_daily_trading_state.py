from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.db import Base
from app.services.daily_trading_state import DailyTradingStateService


@pytest.mark.asyncio
async def test_recompute_from_broker_equity_is_equity_first() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_maker() as session:
        svc = DailyTradingStateService(session)

        state = await svc.recompute_from_broker_equity("bot-1", 10000.0)
        await session.commit()
        assert float(state.starting_equity or 0.0) == 10000.0
        assert float(state.current_equity or 0.0) == 10000.0
        assert float(state.daily_profit_amount or 0.0) == 0.0
        assert float(state.daily_loss_pct or 0.0) == 0.0

        state = await svc.recompute_from_broker_equity("bot-1", 9700.0)
        await session.commit()
        assert float(state.starting_equity or 0.0) == 10000.0
        assert float(state.current_equity or 0.0) == 9700.0
        assert float(state.daily_profit_amount or 0.0) == -300.0
        assert round(float(state.daily_loss_pct or 0.0), 2) == 3.0