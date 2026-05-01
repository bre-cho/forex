from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import token_revocation
from app.core.db import Base, get_db
from app.routers import action_approvals, auth, bots, broker_connections, strategies, workspaces


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
async def test_strategy_live_mutation_blocked_and_promote_with_approval(monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(workspaces.router)
    app.include_router(action_approvals.router)
    app.include_router(strategies.router)
    app.include_router(broker_connections.router)
    app.include_router(bots.router)

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

    monkeypatch.setattr(token_revocation, "get_redis", _get_fake_redis)
    monkeypatch.setattr(auth, "hash_password", lambda raw: f"hashed::{raw}")
    monkeypatch.setattr(auth, "verify_password", lambda raw, hashed: hashed == f"hashed::{raw}")
    app.dependency_overrides[get_db] = _override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        tokens = await _register_and_login(client, "strategy-gov@example.com")
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        ws_resp = await client.post(
            "/v1/workspaces",
            headers=headers,
            json={"name": "Strategy Gov", "slug": "ws-strategy-gov"},
        )
        assert ws_resp.status_code == 201
        workspace_id = ws_resp.json()["id"]

        strategy_resp = await client.post(
            f"/v1/workspaces/{workspace_id}/strategies",
            headers=headers,
            json={"name": "Wave", "description": "v1", "config": {"alpha": 1}},
        )
        assert strategy_resp.status_code == 201
        strategy_id = strategy_resp.json()["id"]

        conn_resp = await client.post(
            f"/v1/workspaces/{workspace_id}/broker-connections",
            headers=headers,
            json={
                "name": "cTrader Demo",
                "broker_type": "ctrader",
                "credential_scope": "demo",
                "credentials": {
                    "client_id": "demo",
                    "client_secret": "demo",
                    "access_token": "demo",
                    "refresh_token": "demo",
                    "account_id": 1,
                },
            },
        )
        assert conn_resp.status_code == 201
        conn_id = conn_resp.json()["id"]

        bot_resp = await client.post(
            f"/v1/workspaces/{workspace_id}/bots",
            headers=headers,
            json={
                "name": "Live Bot",
                "symbol": "EURUSD",
                "timeframe": "M5",
                "mode": "live",
                "strategy_id": strategy_id,
                "broker_connection_id": conn_id,
            },
        )
        assert bot_resp.status_code == 201

        blocked_update = await client.patch(
            f"/v1/workspaces/{workspace_id}/strategies/{strategy_id}",
            headers=headers,
            json={"config": {"alpha": 2}},
        )
        assert blocked_update.status_code == 409
        assert blocked_update.json()["detail"] == "live_strategy_mutation_blocked_create_new_version"

        v2_resp = await client.post(
            f"/v1/workspaces/{workspace_id}/strategies/{strategy_id}/versions",
            headers=headers,
            json={"config_snapshot": {"alpha": 2}, "change_notes": "promote candidate"},
        )
        assert v2_resp.status_code == 200
        assert v2_resp.json()["version"] == 2

        approval_req = await client.post(
            f"/v1/workspaces/{workspace_id}/approvals",
            headers=headers,
            json={
                "action_type": "promote_strategy_live",
                "reason": "approve v2 for live",
            },
        )
        assert approval_req.status_code == 200
        approval_id = int(approval_req.json()["id"])
        approval_ok = await client.post(
            f"/v1/workspaces/{workspace_id}/approvals/{approval_id}/approve",
            headers=headers,
            json={"note": "risk approved"},
        )
        assert approval_ok.status_code == 200

        promote_live = await client.post(
            f"/v1/workspaces/{workspace_id}/strategies/{strategy_id}/versions/2/promote",
            headers=headers,
            json={"stage": "LIVE_APPROVED", "approval_id": approval_id},
        )
        assert promote_live.status_code == 200
        assert promote_live.json()["stage"] == "LIVE_APPROVED"
