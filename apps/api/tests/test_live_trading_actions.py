from __future__ import annotations

from datetime import date

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.db import Base, get_db
from app.dependencies.auth import get_current_user
from app.models import BotInstance, BrokerReconciliationRun, DailyTradingState, TradingIncident, User
from app.routers import live_trading


class _FakeRuntime:
    def __init__(self) -> None:
        self.state = type("State", (), {"metadata": {}, "error_message": ""})()

    async def reconcile_now(self) -> dict:
        return {
            "bot_instance_id": "bot-1",
            "status": "ok",
            "open_positions_broker": 1,
            "open_positions_db": 1,
            "mismatches": [],
            "repaired": 0,
        }


class _FakeRegistry:
    def __init__(self, runtime: _FakeRuntime):
        self._runtime = runtime

    def get(self, bot_id: str):
        if bot_id == "bot-1":
            return self._runtime
        return None


def _build_user() -> User:
    return User(email="tester@example.com", hashed_password="hash", full_name="Tester")


@pytest.mark.asyncio
async def test_live_trading_action_endpoints() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = FastAPI()
    app.include_router(live_trading.router)
    app.state.registry = _FakeRegistry(_FakeRuntime())

    async def _override_get_db():
        async with session_maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def _override_user() -> User:
        return _build_user()

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_user

    async with session_maker() as session:
        session.add(
            BotInstance(
                id="bot-1",
                workspace_id="ws-1",
                name="Bot 1",
                symbol="EURUSD",
                timeframe="M5",
                mode="live",
                status="running",
            )
        )
        session.add(
            DailyTradingState(
                bot_instance_id="bot-1",
                trading_day=date.today(),
                locked=True,
                lock_reason="reconciliation_incident",
            )
        )
        session.add(
            BrokerReconciliationRun(
                bot_instance_id="bot-1",
                status="ok",
                open_positions_broker=1,
                open_positions_db=1,
                mismatches=[],
                repaired=0,
            )
        )
        session.add(
            TradingIncident(
                bot_instance_id="bot-1",
                incident_type="reconciliation_mismatch_persists",
                severity="critical",
                title="Mismatch",
                status="open",
            )
        )
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        rec = await client.post("/v1/workspaces/ws-1/bots/bot-1/reconcile-now")
        assert rec.status_code == 200
        assert rec.json()["status"] == "ok"

        rec_runs = await client.get("/v1/workspaces/ws-1/bots/bot-1/reconciliation-runs")
        assert rec_runs.status_code == 200
        assert rec_runs.json()

        incidents = await client.get("/v1/workspaces/ws-1/bots/bot-1/incidents")
        incident_id = incidents.json()[0]["id"]

        res = await client.post(f"/v1/workspaces/ws-1/bots/bot-1/incidents/{incident_id}/resolve")
        assert res.status_code == 200
        assert res.json()["status"] == "resolved"

        reset = await client.post("/v1/workspaces/ws-1/bots/bot-1/daily-state/reset-lock")
        assert reset.status_code == 200
        assert reset.json()["locked"] is False

        kill = await client.post("/v1/workspaces/ws-1/bots/bot-1/kill-switch")
        assert kill.status_code == 200
        assert kill.json()["kill_switch"] is True

        unkill = await client.post("/v1/workspaces/ws-1/bots/bot-1/reset-kill-switch")
        assert unkill.status_code == 200
        assert unkill.json()["kill_switch"] is False
