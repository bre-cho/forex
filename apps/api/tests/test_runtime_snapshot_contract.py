from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import token_revocation
from app.core.db import Base, get_db
from app.routers import auth, bots, workspaces


class _FakeRedis:
    async def get(self, _key: str):
        return None


async def _register_and_login(client: AsyncClient, email: str) -> dict:
    await client.post(
        "/v1/auth/register",
        json={"email": email, "password": "StrongPass123!", "full_name": "User"},
    )
    login = await client.post(
        "/v1/auth/login",
        json={"email": email, "password": "StrongPass123!"},
    )
    return login.json()


@pytest.mark.asyncio
async def test_runtime_endpoint_not_running_returns_full_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(workspaces.router)
    app.include_router(bots.router)
    app.state.registry = None

    async def _override_get_db():
        async with session_maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    fake_redis = _FakeRedis()

    async def _get_fake_redis():
        return fake_redis

    monkeypatch.setattr(auth, "hash_password", lambda raw: f"hashed::{raw}")
    monkeypatch.setattr(auth, "verify_password", lambda raw, hashed: hashed == f"hashed::{raw}")
    monkeypatch.setattr(token_revocation, "get_redis", _get_fake_redis)
    app.dependency_overrides[get_db] = _override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        tokens = await _register_and_login(client, "runtime-contract@example.com")
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        ws = await client.post(
            "/v1/workspaces",
            headers=headers,
            json={"name": "Runtime Contract", "slug": "runtime-contract"},
        )
        ws_id = ws.json()["id"]

        bot = await client.post(
            f"/v1/workspaces/{ws_id}/bots",
            headers=headers,
            json={"name": "Bot", "symbol": "EURUSD", "timeframe": "M5", "mode": "paper"},
        )
        bot_id = bot.json()["id"]

        resp = await client.get(
            f"/v1/workspaces/{ws_id}/bots/{bot_id}/runtime",
            headers=headers,
        )
        assert resp.status_code == 200
        payload = resp.json()

        required = {
            "bot_instance_id",
            "status",
            "started_at",
            "stopped_at",
            "balance",
            "equity",
            "daily_pnl",
            "open_trades",
            "total_trades",
            "error_message",
            "metadata",
            "uptime_seconds",
        }
        assert required.issubset(set(payload.keys()))
        assert payload["status"] == "not_running"
        assert payload["bot_instance_id"] == bot_id
