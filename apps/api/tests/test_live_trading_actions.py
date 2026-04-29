from __future__ import annotations

from datetime import date

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.db import Base, get_db
from app.dependencies.auth import get_current_user
from app.models import (
    BotInstance,
    BrokerAccountSnapshot,
    BrokerExecutionReceipt,
    BrokerReconciliationRun,
    DailyTradingState,
    StrategyExperiment,
    TradingIncident,
    User,
)
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

    async def get_snapshot(self) -> dict:
        return {"status": "running", "metadata": {"market_data_ok": True}}


class _FakeRegistry:
    def __init__(self, runtime: _FakeRuntime):
        self._runtime = runtime

    def get(self, bot_id: str):
        if bot_id == "bot-1":
            return self._runtime
        return None


def _build_user() -> User:
    return User(email="tester@example.com", hashed_password="hash", full_name="Tester", is_superuser=True)


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
        session.add(
            BrokerAccountSnapshot(
                bot_instance_id="bot-1",
                broker="ctrader",
                account_id="acc-1",
                balance=1000.0,
                equity=1002.0,
                margin=20.0,
                free_margin=982.0,
                margin_level=5010.0,
                currency="USD",
                raw_response={},
            )
        )
        session.add(
            BrokerExecutionReceipt(
                bot_instance_id="bot-1",
                idempotency_key="idem-1",
                broker="ctrader",
                broker_order_id="ord-1",
                broker_position_id="pos-1",
                broker_deal_id="deal-1",
                submit_status="ACKED",
                fill_status="FILLED",
                requested_volume=0.01,
                filled_volume=0.01,
                avg_fill_price=1.2,
                commission=0.0,
                raw_response={},
            )
        )
        session.add(
            StrategyExperiment(
                bot_instance_id="bot-1",
                version=1,
                stage="DEMO_TEST",
                strategy_snapshot={"name": "wave"},
                policy_snapshot={"risk": 1},
                metrics_snapshot={"winrate": 0.55},
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

        receipts = await client.get("/v1/workspaces/ws-1/bots/bot-1/execution-receipts")
        assert receipts.status_code == 200

        ops = await client.get("/v1/workspaces/ws-1/bots/bot-1/operations-dashboard")
        assert ops.status_code == 200
        payload = ops.json()
        assert payload["runtime"]["status"] == "running"
        assert payload["latest_account_snapshot"]["currency"] == "USD"
        assert payload["latest_experiment"]["stage"] == "DEMO_TEST"

        incidents = await client.get("/v1/workspaces/ws-1/bots/bot-1/incidents")
        incident_id = incidents.json()[0]["id"]

        res = await client.post(f"/v1/workspaces/ws-1/bots/bot-1/incidents/{incident_id}/resolve")
        assert res.status_code == 200
        assert res.json()["status"] == "resolved"

        reset = await client.post(
            "/v1/workspaces/ws-1/bots/bot-1/daily-state/reset-lock",
            json={"reason": "incident_resolved"},
        )
        assert reset.status_code == 200
        assert reset.json()["locked"] is False

        bad_reset = await client.post(
            "/v1/workspaces/ws-1/bots/bot-1/daily-state/reset-lock",
            json={},
        )
        assert bad_reset.status_code == 400

        kill = await client.post("/v1/workspaces/ws-1/bots/bot-1/kill-switch")
        assert kill.status_code == 200
        assert kill.json()["kill_switch"] is True

        unkill = await client.post("/v1/workspaces/ws-1/bots/bot-1/reset-kill-switch")
        assert unkill.status_code == 200
        assert unkill.json()["kill_switch"] is False
