from __future__ import annotations

from datetime import date

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.db import Base, get_db
from app.dependencies.auth import get_current_user
from app.models import (
    ActionApprovalRequest,
    AuditLog,
    BotInstance,
    DailyLockAction,
    Order,
    TradingIncident,
    User,
)
from app.routers import compliance


def _build_user() -> User:
    return User(email="admin@example.com", hashed_password="hash", full_name="Admin", is_superuser=True)


@pytest.mark.asyncio
async def test_compliance_export_json_and_csv() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = FastAPI()
    app.include_router(compliance.router)

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
            AuditLog(
                user_id="user-1",
                action="live_start",
                resource_type="bot_instance",
                resource_id="bot-1",
                details={"workspace_id": "ws-1", "bot_id": "bot-1"},
            )
        )
        session.add(
            TradingIncident(
                bot_instance_id="bot-1",
                incident_type="risk_breach",
                severity="critical",
                title="Risk",
                status="open",
            )
        )
        session.add(
            Order(
                id="ord-1",
                bot_instance_id="bot-1",
                symbol="EURUSD",
                side="buy",
                order_type="market",
                volume=0.01,
                status="filled",
                idempotency_key="idem-1",
            )
        )
        session.add(
            DailyLockAction(
                bot_instance_id="bot-1",
                trading_day=date.today(),
                lock_action="close_all_and_stop",
                status="completed",
            )
        )
        session.add(
            ActionApprovalRequest(
                workspace_id="ws-1",
                bot_instance_id="bot-1",
                action_type="start_live_bot",
                status="approved",
                reason="approved",
            )
        )
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        json_resp = await client.get("/v1/workspaces/ws-1/compliance/export", params={"format": "json", "days": 30})
        assert json_resp.status_code == 200
        payload = json_resp.json()
        assert payload["workspace_id"] == "ws-1"
        assert payload["counts"]["orders"] == 1
        assert payload["counts"]["incidents"] == 1

        csv_resp = await client.get("/v1/workspaces/ws-1/compliance/export", params={"format": "csv", "days": 30})
        assert csv_resp.status_code == 200
        assert "record_type,record_id,timestamp" in csv_resp.text
        assert "incident" in csv_resp.text
        assert "approval" in csv_resp.text
