from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.db import Base, get_db
from app.routers import auth


class _FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def setex(self, key: str, _ttl: int, value: str) -> None:
        self._store[key] = value

    async def exists(self, key: str) -> int:
        return 1 if key in self._store else 0

    async def set(self, key: str, value: int) -> None:
        self._store[key] = str(value)

    async def get(self, key: str):
        return self._store.get(key)


@pytest.mark.asyncio
async def test_login_refresh_logout_flow(monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = FastAPI()
    app.include_router(auth.router)

    async def _override_get_db():
        async with session_maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _override_get_db
    fake_redis = _FakeRedis()

    async def _get_fake_redis():
        return fake_redis

    monkeypatch.setattr(auth, "get_redis", _get_fake_redis)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        register_resp = await client.post(
            "/v1/auth/register",
            json={
                "email": "user@example.com",
                "password": "StrongPass123!",
                "full_name": "User Test",
            },
        )
        assert register_resp.status_code == 201

        login_resp = await client.post(
            "/v1/auth/login",
            json={"email": "user@example.com", "password": "StrongPass123!"},
        )
        assert login_resp.status_code == 200
        tokens = login_resp.json()
        assert tokens["access_token"]
        assert tokens["refresh_token"]

        refresh_resp = await client.post(
            "/v1/auth/refresh",
            json={"refresh_token": tokens["refresh_token"]},
        )
        assert refresh_resp.status_code == 200
        refreshed = refresh_resp.json()
        assert refreshed["refresh_token"] != ""

        logout_resp = await client.post(
            "/v1/auth/logout",
            json={"refresh_token": tokens["refresh_token"]},
        )
        assert logout_resp.status_code == 200

        me_after_logout = await client.get(
            "/v1/auth/me",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        assert me_after_logout.status_code == 401

        refresh_after_logout = await client.post(
            "/v1/auth/refresh",
            json={"refresh_token": tokens["refresh_token"]},
        )
        assert refresh_after_logout.status_code == 401


@pytest.mark.asyncio
async def test_forgot_reset_revokes_old_tokens(monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = FastAPI()
    app.include_router(auth.router)

    async def _override_get_db():
        async with session_maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _override_get_db
    fake_redis = _FakeRedis()

    async def _get_fake_redis():
        return fake_redis

    captured: dict[str, str] = {}

    async def _capture_reset_email(_email: str, reset_token: str) -> None:
        captured["token"] = reset_token

    monkeypatch.setattr(auth, "get_redis", _get_fake_redis)
    monkeypatch.setattr(auth, "_send_reset_email", _capture_reset_email)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        register_resp = await client.post(
            "/v1/auth/register",
            json={
                "email": "reset@example.com",
                "password": "StrongPass123!",
                "full_name": "Reset User",
            },
        )
        assert register_resp.status_code == 201

        login_resp = await client.post(
            "/v1/auth/login",
            json={"email": "reset@example.com", "password": "StrongPass123!"},
        )
        assert login_resp.status_code == 200
        old_tokens = login_resp.json()

        forgot_resp = await client.post(
            "/v1/auth/forgot-password",
            json={"email": "reset@example.com"},
        )
        assert forgot_resp.status_code == 200
        assert captured["token"]

        reset_resp = await client.post(
            "/v1/auth/reset-password",
            json={"token": captured["token"], "new_password": "NewStrongPass456!"},
        )
        assert reset_resp.status_code == 200

        old_me = await client.get(
            "/v1/auth/me",
            headers={"Authorization": f"Bearer {old_tokens['access_token']}"},
        )
        assert old_me.status_code == 401

        old_refresh = await client.post(
            "/v1/auth/refresh",
            json={"refresh_token": old_tokens["refresh_token"]},
        )
        assert old_refresh.status_code == 401

        old_login = await client.post(
            "/v1/auth/login",
            json={"email": "reset@example.com", "password": "StrongPass123!"},
        )
        assert old_login.status_code == 401

        new_login = await client.post(
            "/v1/auth/login",
            json={"email": "reset@example.com", "password": "NewStrongPass456!"},
        )
        assert new_login.status_code == 200
