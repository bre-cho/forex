from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import token_revocation
from app.core.db import Base, get_db
from app.routers import auth, bots, workspaces
from app.services import bot_service


class _Runtime:
    def __init__(self) -> None:
        self.running = False
        self.paused = False


class _FakeRegistry:
    def __init__(self) -> None:
        self._runtimes: dict[str, _Runtime] = {}

    def get(self, bot_id: str):
        return self._runtimes.get(bot_id)

    async def start(self, bot_id: str):
        runtime = self._runtimes.setdefault(bot_id, _Runtime())
        runtime.running = True
        runtime.paused = False

    async def stop(self, bot_id: str):
        runtime = self._runtimes.setdefault(bot_id, _Runtime())
        runtime.running = False
        runtime.paused = False

    async def pause(self, bot_id: str):
        runtime = self._runtimes.setdefault(bot_id, _Runtime())
        if runtime.running:
            runtime.paused = True

    async def resume(self, bot_id: str):
        runtime = self._runtimes.setdefault(bot_id, _Runtime())
        runtime.running = True
        runtime.paused = False

    async def remove(self, bot_id: str):
        self._runtimes.pop(bot_id, None)


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
async def test_workspace_isolation_and_bot_lifecycle_idempotency(monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(workspaces.router)
    app.include_router(bots.router)
    app.state.registry = _FakeRegistry()

    async def _override_get_db():
        async with session_maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def _fake_create_runtime_for_bot(bot, registry, db):
        registry._runtimes.setdefault(bot.id, _Runtime())

    monkeypatch.setattr(bot_service, "create_runtime_for_bot", _fake_create_runtime_for_bot)
    fake_redis = _FakeRedis()

    async def _get_fake_redis():
        return fake_redis

    monkeypatch.setattr(token_revocation, "get_redis", _get_fake_redis)
    app.dependency_overrides[get_db] = _override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        user1_tokens = await _register_and_login(client, "owner1@example.com")
        user2_tokens = await _register_and_login(client, "owner2@example.com")

        user1_headers = {"Authorization": f"Bearer {user1_tokens['access_token']}"}
        user2_headers = {"Authorization": f"Bearer {user2_tokens['access_token']}"}

        ws1 = await client.post(
            "/v1/workspaces",
            headers=user1_headers,
            json={"name": "Workspace 1", "slug": "ws-owner1"},
        )
        ws1_id = ws1.json()["id"]

        ws2 = await client.post(
            "/v1/workspaces",
            headers=user2_headers,
            json={"name": "Workspace 2", "slug": "ws-owner2"},
        )
        ws2_id = ws2.json()["id"]

        create_bot_resp = await client.post(
            f"/v1/workspaces/{ws1_id}/bots",
            headers=user1_headers,
            json={"name": "Bot A", "symbol": "EURUSD", "timeframe": "M5", "mode": "paper"},
        )
        assert create_bot_resp.status_code == 201
        bot_id = create_bot_resp.json()["id"]

        isolation_resp = await client.get(
            f"/v1/workspaces/{ws1_id}/bots/{bot_id}",
            headers=user2_headers,
        )
        assert isolation_resp.status_code == 403

        workspace_read_isolation = await client.get(
            f"/v1/workspaces/{ws1_id}",
            headers=user2_headers,
        )
        assert workspace_read_isolation.status_code == 403

        start_first = await client.post(
            f"/v1/workspaces/{ws1_id}/bots/{bot_id}/start",
            headers=user1_headers,
        )
        assert start_first.status_code == 200
        assert start_first.json()["already_in_state"] is False

        start_second = await client.post(
            f"/v1/workspaces/{ws1_id}/bots/{bot_id}/start",
            headers=user1_headers,
        )
        assert start_second.status_code == 200
        assert start_second.json()["already_in_state"] is True

        stop_first = await client.post(
            f"/v1/workspaces/{ws1_id}/bots/{bot_id}/stop",
            headers=user1_headers,
        )
        assert stop_first.status_code == 200
        assert stop_first.json()["already_in_state"] is False

        stop_second = await client.post(
            f"/v1/workspaces/{ws1_id}/bots/{bot_id}/stop",
            headers=user1_headers,
        )
        assert stop_second.status_code == 200
        assert stop_second.json()["already_in_state"] is True

        add_viewer_resp = await client.post(
            f"/v1/workspaces/{ws1_id}/members",
            headers=user1_headers,
            json={"email": "owner2@example.com", "role": "viewer"},
        )
        assert add_viewer_resp.status_code == 200
        owner2_user_id = add_viewer_resp.json()["user_id"]

        viewer_start_resp = await client.post(
            f"/v1/workspaces/{ws1_id}/bots/{bot_id}/start",
            headers=user2_headers,
        )
        assert viewer_start_resp.status_code == 403

        user3_tokens = await _register_and_login(client, "owner3@example.com")
        user3_headers = {"Authorization": f"Bearer {user3_tokens['access_token']}"}

        remove_viewer_resp = await client.delete(
            f"/v1/workspaces/{ws1_id}/members/{owner2_user_id}",
            headers=user1_headers,
        )
        assert remove_viewer_resp.status_code == 204

        add_admin_resp = await client.post(
            f"/v1/workspaces/{ws1_id}/members",
            headers=user1_headers,
            json={"email": "owner2@example.com", "role": "admin"},
        )
        assert add_admin_resp.status_code == 200

        admin_add_member_resp = await client.post(
            f"/v1/workspaces/{ws1_id}/members",
            headers=user2_headers,
            json={"email": "owner3@example.com", "role": "viewer"},
        )
        assert admin_add_member_resp.status_code == 200

        user3_workspace_access = await client.get(
            f"/v1/workspaces/{ws1_id}",
            headers=user3_headers,
        )
        assert user3_workspace_access.status_code == 200

        list_other_workspace = await client.get(
            f"/v1/workspaces/{ws2_id}/bots",
            headers=user1_headers,
        )
        assert list_other_workspace.status_code == 403
