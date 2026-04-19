from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import token_revocation
from app.core.db import Base, get_db
from app.models import Trade
from app.routers import auth, bots, public, strategies, workspaces


class _FakeRedis:
    async def get(self, _key: str):
        return None


async def _get_fake_redis():
    return _FakeRedis()


async def register_and_login(client: AsyncClient, email: str) -> tuple[dict, dict]:
    await client.post(
        "/v1/auth/register",
        json={"email": email, "password": "StrongPass123!", "full_name": "User"},
    )
    login = await client.post(
        "/v1/auth/login",
        json={"email": email, "password": "StrongPass123!"},
    )
    tokens = login.json()
    return tokens, {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.mark.asyncio
async def test_public_strategies_do_not_expose_workspace_or_config(monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(workspaces.router)
    app.include_router(strategies.router)
    app.include_router(public.router)

    async def _override_get_db():
        async with session_maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr(token_revocation, "get_redis", _get_fake_redis)
    app.dependency_overrides[get_db] = _override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _, headers = await register_and_login(client, "public@example.com")
        workspace_resp = await client.post(
            "/v1/workspaces",
            headers=headers,
            json={"name": "Public WS", "slug": "public-ws"},
        )
        workspace_id = workspace_resp.json()["id"]

        strategy_resp = await client.post(
            f"/v1/workspaces/{workspace_id}/strategies",
            headers=headers,
            json={
                "name": "Public Strat",
                "description": "Public listing",
                "is_public": True,
                "config": {"secret_token": "do-not-leak"},
            },
        )
        assert strategy_resp.status_code == 201

        public_resp = await client.get("/v1/public/strategies")
        assert public_resp.status_code == 200
        items = public_resp.json()
        assert items
        for item in items:
            assert "workspace_id" not in item
            assert "config" not in item


@pytest.mark.asyncio
async def test_public_leaderboard_aggregates_without_error(monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(workspaces.router)
    app.include_router(strategies.router)
    app.include_router(bots.router)
    app.include_router(public.router)

    async def _override_get_db():
        async with session_maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr(token_revocation, "get_redis", _get_fake_redis)
    app.dependency_overrides[get_db] = _override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _, headers = await register_and_login(client, "leaderboard@example.com")
        workspace_resp = await client.post(
            "/v1/workspaces",
            headers=headers,
            json={"name": "Leaderboard WS", "slug": "leaderboard-ws"},
        )
        workspace_id = workspace_resp.json()["id"]

        strategy_resp = await client.post(
            f"/v1/workspaces/{workspace_id}/strategies",
            headers=headers,
            json={"name": "Ranked Strat", "description": "Ranked", "is_public": True, "config": {}},
        )
        strategy_id = strategy_resp.json()["id"]

        bot_resp = await client.post(
            f"/v1/workspaces/{workspace_id}/bots",
            headers=headers,
            json={
                "name": "Leaderboard Bot",
                "symbol": "EURUSD",
                "timeframe": "M5",
                "mode": "paper",
                "strategy_id": strategy_id,
            },
        )
        bot_id = bot_resp.json()["id"]

    async with session_maker() as session:
        session.add(
            Trade(
                bot_instance_id=bot_id,
                broker_trade_id="t-1",
                symbol="EURUSD",
                side="buy",
                volume=0.1,
                entry_price=1.1000,
                exit_price=1.1020,
                pnl=20.0,
            )
        )
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        leaderboard_resp = await client.get("/v1/public/performance/leaderboard")
        assert leaderboard_resp.status_code == 200
        payload = leaderboard_resp.json()
        assert payload["count"] == 1
        item = payload["items"][0]
        assert item["bot_name"] == "Leaderboard Bot"
        assert item["total_trades"] == 1
        assert item["total_pnl"] == 20.0
        assert item["win_rate"] == 1.0
