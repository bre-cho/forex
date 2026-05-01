from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import token_revocation
from app.core.db import Base, get_db
from app.routers import action_approvals, auth, bots, broker_connections, workspaces
from app.services import bot_service


class _Runtime:
    def __init__(self) -> None:
        self.running = False
        self.paused = False

    async def get_snapshot(self) -> dict:
        status = "running" if self.running else "stopped"
        return {"status": status, "metadata": {}}


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
    app.include_router(action_approvals.router)
    app.include_router(broker_connections.router)
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
        runtime = _Runtime()
        runtime.broker_provider = object()
        registry._runtimes.setdefault(bot.id, runtime)

    monkeypatch.setattr(bot_service, "create_runtime_for_bot", _fake_create_runtime_for_bot)
    fake_redis = _FakeRedis()

    async def _get_fake_redis():
        return fake_redis

    monkeypatch.setattr(token_revocation, "get_redis", _get_fake_redis)
    app.dependency_overrides[get_db] = _override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        user1_tokens = await _register_and_login(client, "owner1@example.com")
        user2_tokens = await _register_and_login(client, "user2@example.com")

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
            json={"email": "user2@example.com", "role": "viewer"},
        )
        assert add_viewer_resp.status_code == 200
        owner2_user_id = add_viewer_resp.json()["user_id"]

        viewer_start_resp = await client.post(
            f"/v1/workspaces/{ws1_id}/bots/{bot_id}/start",
            headers=user2_headers,
        )
        assert viewer_start_resp.status_code == 403

        user3_tokens = await _register_and_login(client, "user3@example.com")
        user3_headers = {"Authorization": f"Bearer {user3_tokens['access_token']}"}

        remove_viewer_resp = await client.delete(
            f"/v1/workspaces/{ws1_id}/members/{owner2_user_id}",
            headers=user1_headers,
        )
        assert remove_viewer_resp.status_code == 204

        add_admin_resp = await client.post(
            f"/v1/workspaces/{ws1_id}/members",
            headers=user1_headers,
            json={"email": "user2@example.com", "role": "admin"},
        )
        assert add_admin_resp.status_code == 200

        admin_add_member_resp = await client.post(
            f"/v1/workspaces/{ws1_id}/members",
            headers=user2_headers,
            json={"email": "user3@example.com", "role": "viewer"},
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


@pytest.mark.asyncio
async def test_live_start_hard_fails_when_guard_blocks_runtime(monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(workspaces.router)
    app.include_router(action_approvals.router)
    app.include_router(broker_connections.router)
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
        runtime = _Runtime()
        runtime.broker_provider = object()
        registry._runtimes.setdefault(bot.id, runtime)

    async def _force_guard_failure(bot, registry):
        raise RuntimeError("Live runtime guard blocked")

    async def _ok_provider(_provider, require_live=True):
        return type("Readiness", (), {"ok": True, "reason": ""})()

    async def _ok_preflight(*, bot, provider, db):
        return {"broker_health": True, "active_policy": True, "daily_state_fresh": True, "no_critical_incident": True}

    monkeypatch.setattr(bot_service, "create_runtime_for_bot", _fake_create_runtime_for_bot)
    monkeypatch.setattr(bot_service, "assert_runtime_live_guard", _force_guard_failure)
    monkeypatch.setattr(bots.LiveReadinessGuard, "check_provider", _ok_provider)
    monkeypatch.setattr(bots, "run_live_start_preflight", _ok_preflight)
    monkeypatch.setattr(auth, "hash_password", lambda raw: f"hashed::{raw}")
    monkeypatch.setattr(auth, "verify_password", lambda raw, hashed: hashed == f"hashed::{raw}")

    fake_redis = _FakeRedis()

    async def _get_fake_redis():
        return fake_redis

    monkeypatch.setattr(token_revocation, "get_redis", _get_fake_redis)
    app.dependency_overrides[get_db] = _override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        tokens = await _register_and_login(client, "live-guard@example.com")
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        ws_resp = await client.post(
            "/v1/workspaces",
            headers=headers,
            json={"name": "Live Guard", "slug": "ws-live-guard"},
        )
        workspace_id = ws_resp.json()["id"]

        conn_resp = await client.post(
            f"/v1/workspaces/{workspace_id}/broker-connections",
            headers=headers,
            json={
                "name": "cTrader Demo",
                "broker_type": "ctrader",
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
        broker_connection_id = conn_resp.json()["id"]

        bot_resp = await client.post(
            f"/v1/workspaces/{workspace_id}/bots",
            headers=headers,
            json={
                "name": "Live Bot",
                "symbol": "EURUSD",
                "timeframe": "M5",
                "mode": "live",
                "broker_connection_id": broker_connection_id,
            },
        )
        assert bot_resp.status_code == 201
        bot_id = bot_resp.json()["id"]

        missing_approval = await client.post(
            f"/v1/workspaces/{workspace_id}/bots/{bot_id}/start",
            headers=headers,
            json={"reason": "live_guard_validation"},
        )
        assert missing_approval.status_code == 403
        assert "approval_required:start_live_bot" in missing_approval.json()["detail"]

        approval_req = await client.post(
            f"/v1/workspaces/{workspace_id}/approvals",
            headers=headers,
            json={
                "action_type": "start_live_bot",
                "bot_id": bot_id,
                "reason": "live_guard_validation",
            },
        )
        assert approval_req.status_code == 200
        approval_id = int(approval_req.json()["id"])
        approval_ok = await client.post(
            f"/v1/workspaces/{workspace_id}/approvals/{approval_id}/approve",
            headers=headers,
            json={"note": "approved_for_test"},
        )
        assert approval_ok.status_code == 200

        start_resp = await client.post(
            f"/v1/workspaces/{workspace_id}/bots/{bot_id}/start",
            headers=headers,
            json={"reason": "live_guard_validation", "approval_id": approval_id},
        )
        assert start_resp.status_code == 503
        assert "guard blocked" in start_resp.json()["detail"].lower()

        bot_state = await client.get(
            f"/v1/workspaces/{workspace_id}/bots/{bot_id}",
            headers=headers,
        )
        assert bot_state.status_code == 200
        assert bot_state.json()["status"] in {"stopped", "error"}


@pytest.mark.asyncio
async def test_live_start_hard_fails_when_preflight_blocks(monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(workspaces.router)
    app.include_router(action_approvals.router)
    app.include_router(broker_connections.router)
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

    class _LiveRuntime(_Runtime):
        def __init__(self) -> None:
            super().__init__()
            self.broker_provider = object()

    async def _fake_create_runtime_for_bot(bot, registry, db):
        registry._runtimes.setdefault(bot.id, _LiveRuntime())

    async def _ok_provider(_provider, require_live=True):
        return type("Readiness", (), {"ok": True, "reason": ""})()

    async def _fail_preflight(*, bot, provider, db):
        raise bots.LiveStartPreflightError("daily_state_stale")

    monkeypatch.setattr(bot_service, "create_runtime_for_bot", _fake_create_runtime_for_bot)
    monkeypatch.setattr(bots.LiveReadinessGuard, "check_provider", _ok_provider)
    monkeypatch.setattr(bots, "run_live_start_preflight", _fail_preflight)
    monkeypatch.setattr(auth, "hash_password", lambda raw: f"hashed::{raw}")
    monkeypatch.setattr(auth, "verify_password", lambda raw, hashed: hashed == f"hashed::{raw}")

    fake_redis = _FakeRedis()

    async def _get_fake_redis():
        return fake_redis

    monkeypatch.setattr(token_revocation, "get_redis", _get_fake_redis)
    app.dependency_overrides[get_db] = _override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        tokens = await _register_and_login(client, "live-preflight@example.com")
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        ws_resp = await client.post(
            "/v1/workspaces",
            headers=headers,
            json={"name": "Live Preflight", "slug": "ws-live-preflight"},
        )
        workspace_id = ws_resp.json()["id"]

        conn_resp = await client.post(
            f"/v1/workspaces/{workspace_id}/broker-connections",
            headers=headers,
            json={
                "name": "cTrader Demo",
                "broker_type": "ctrader",
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
        broker_connection_id = conn_resp.json()["id"]

        bot_resp = await client.post(
            f"/v1/workspaces/{workspace_id}/bots",
            headers=headers,
            json={
                "name": "Live Bot",
                "symbol": "EURUSD",
                "timeframe": "M5",
                "mode": "live",
                "broker_connection_id": broker_connection_id,
            },
        )
        assert bot_resp.status_code == 201
        bot_id = bot_resp.json()["id"]

        approval_req = await client.post(
            f"/v1/workspaces/{workspace_id}/approvals",
            headers=headers,
            json={
                "action_type": "start_live_bot",
                "bot_id": bot_id,
                "reason": "preflight_validation",
            },
        )
        assert approval_req.status_code == 200
        approval_id = int(approval_req.json()["id"])
        approval_ok = await client.post(
            f"/v1/workspaces/{workspace_id}/approvals/{approval_id}/approve",
            headers=headers,
            json={"note": "approved_for_test"},
        )
        assert approval_ok.status_code == 200

        start_resp = await client.post(
            f"/v1/workspaces/{workspace_id}/bots/{bot_id}/start",
            headers=headers,
            json={"reason": "preflight_validation", "approval_id": approval_id},
        )
        assert start_resp.status_code == 503
        assert "daily_state_stale" in start_resp.json()["detail"]


@pytest.mark.asyncio
async def test_bot_readiness_endpoint_returns_modes(monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(workspaces.router)
    app.include_router(action_approvals.router)
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

    fake_redis = _FakeRedis()

    async def _get_fake_redis():
        return fake_redis

    monkeypatch.setattr(token_revocation, "get_redis", _get_fake_redis)
    app.dependency_overrides[get_db] = _override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        tokens = await _register_and_login(client, "readiness@example.com")
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        ws_resp = await client.post(
            "/v1/workspaces",
            headers=headers,
            json={"name": "Readiness", "slug": "ws-readiness"},
        )
        workspace_id = ws_resp.json()["id"]

        bot_resp = await client.post(
            f"/v1/workspaces/{workspace_id}/bots",
            headers=headers,
            json={"name": "Paper Bot", "symbol": "EURUSD", "timeframe": "M5", "mode": "paper"},
        )
        assert bot_resp.status_code == 201
        bot_id = bot_resp.json()["id"]

        readiness_resp = await client.get(
            f"/v1/workspaces/{workspace_id}/bots/{bot_id}/readiness",
            headers=headers,
        )
        assert readiness_resp.status_code == 200
        payload = readiness_resp.json()
        assert payload["bot_mode"] == "paper"
        assert payload["runtime_mode"] == "not_running"
        assert payload["provider_mode"] in {"paper", "unknown"}
        assert payload["llm_mode"] in {"openai", "gemini", "stub"}
        assert payload["ready_for_live_trading"] is False
