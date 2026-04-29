from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.db import Base, get_db
from app.dependencies.auth import get_current_user
from app.models import User
from app.routers import risk_policy
from app.services.policy_service import PolicyService


def _build_admin() -> User:
    return User(email="admin@example.com", hashed_password="hash", full_name="Admin", is_superuser=True)


@pytest.mark.asyncio
async def test_policy_draft_approve_activate_flow() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = FastAPI()
    app.include_router(risk_policy.router)

    async def _override_get_db():
        async with session_maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def _override_user() -> User:
        return _build_admin()

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_user

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        draft = await client.post(
            "/v1/workspaces/ws-1/bots/bot-1/risk-policy/draft",
            json={"policy_snapshot": {"max_daily_loss_pct": 3.0}, "change_reason": "tighten risk"},
        )
        assert draft.status_code == 200
        version = int(draft.json()["version"])

        approve = await client.post(
            "/v1/workspaces/ws-1/bots/bot-1/risk-policy/approve",
            json={"version": version, "note": "approved for live"},
        )
        assert approve.status_code == 200

        activate = await client.post(
            "/v1/workspaces/ws-1/bots/bot-1/risk-policy/activate",
            json={"version": version, "note": "activate now"},
        )
        assert activate.status_code == 200

        active = await client.get("/v1/workspaces/ws-1/bots/bot-1/risk-policy/active")
        assert active.status_code == 200
        assert active.json()["approved_for_live"] is True

    async with session_maker() as session:
        svc = PolicyService(session)
        assert await svc.is_policy_approved_for_live("bot-1") is True
