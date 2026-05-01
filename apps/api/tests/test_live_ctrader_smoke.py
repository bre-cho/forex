from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import token_revocation
from app.core.db import Base, get_db
from app.events import publishers
from app.routers import auth, bots, broker_connections, signals, workspaces, ws
from app.services import bot_service


_REQUIRED_ENV = [
    "RUN_LIVE_CTRADER_SMOKE",
    "CTRADER_CLIENT_ID",
    "CTRADER_CLIENT_SECRET",
    "CTRADER_ACCESS_TOKEN",
    "CTRADER_REFRESH_TOKEN",
    "CTRADER_ACCOUNT_ID",
]


class _FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self.published: list[tuple[str, str]] = []

    async def setex(self, key: str, _ttl: int, value: str) -> None:
        self._store[key] = value

    async def exists(self, key: str) -> int:
        return 1 if key in self._store else 0

    async def set(self, key: str, value: int) -> None:
        self._store[key] = str(value)

    async def get(self, key: str):
        return self._store.get(key)

    async def publish(self, channel: str, message: str) -> None:
        self.published.append((channel, message))

    def pubsub(self):
        return _FakePubSub(self)


class _FakePubSub:
    def __init__(self, redis: _FakeRedis) -> None:
        self._redis = redis
        self._channels: set[str] = set()

    async def subscribe(self, *channels: str) -> None:
        self._channels.update(channels)

    async def unsubscribe(self, *channels: str) -> None:
        for channel in channels:
            self._channels.discard(channel)

    async def close(self) -> None:
        return

    async def listen(self):
        while True:
            yield {"type": "message", "data": "{}"}


async def _create_all(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def _require_live_env() -> None:
    if os.getenv("RUN_LIVE_CTRADER_SMOKE", "0") != "1":
        pytest.skip("Set RUN_LIVE_CTRADER_SMOKE=1 to run live cTrader smoke test")

    missing = [key for key in _REQUIRED_ENV[1:] if not os.getenv(key)]
    if missing:
        pytest.skip(f"Missing live cTrader env vars: {', '.join(missing)}")


@pytest.mark.smoke
def test_live_ctrader_demo_smoke_e2e(monkeypatch: pytest.MonkeyPatch):
    _require_live_env()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    import asyncio

    asyncio.run(_create_all(engine))

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(workspaces.router)
    app.include_router(broker_connections.router)
    app.include_router(bots.router)
    app.include_router(signals.signals_router)
    app.include_router(signals.orders_router)
    app.include_router(signals.trades_router)
    app.include_router(ws.router)

    from trading_core.runtime import RuntimeRegistry

    app.state.registry = RuntimeRegistry()

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
    monkeypatch.setattr(auth, "hash_password", lambda raw: f"hashed::{raw}")
    monkeypatch.setattr(auth, "verify_password", lambda raw, hashed: hashed == f"hashed::{raw}")
    monkeypatch.setattr(token_revocation, "get_redis", _get_fake_redis)
    monkeypatch.setattr(publishers, "get_redis", _get_fake_redis)
    monkeypatch.setattr(ws, "get_redis", _get_fake_redis)
    monkeypatch.setattr(ws, "AsyncSessionLocal", session_maker)
    monkeypatch.setattr(bot_service, "AsyncSessionLocal", session_maker)

    with TestClient(app) as client:
        register = client.post(
            "/v1/auth/register",
            json={
                "email": "ctrader-smoke@example.com",
                "password": "StrongPass123!",
                "full_name": "cTrader Smoke",
            },
        )
        assert register.status_code == 201

        login = client.post(
            "/v1/auth/login",
            json={"email": "ctrader-smoke@example.com", "password": "StrongPass123!"},
        )
        assert login.status_code == 200
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        ws_create = client.post(
            "/v1/workspaces",
            headers=headers,
            json={"name": "Smoke Live", "slug": "smoke-live-ctrader"},
        )
        assert ws_create.status_code == 201
        workspace_id = ws_create.json()["id"]

        conn_create = client.post(
            f"/v1/workspaces/{workspace_id}/broker-connections",
            headers=headers,
            json={
                "name": "cTrader Demo",
                "broker_type": "ctrader",
                "credentials": {
                    "client_id": os.environ["CTRADER_CLIENT_ID"],
                    "client_secret": os.environ["CTRADER_CLIENT_SECRET"],
                    "access_token": os.environ["CTRADER_ACCESS_TOKEN"],
                    "refresh_token": os.environ["CTRADER_REFRESH_TOKEN"],
                    "account_id": int(os.environ["CTRADER_ACCOUNT_ID"]),
                    "symbol": os.getenv("CTRADER_SYMBOL", "EURUSD"),
                    "timeframe": os.getenv("CTRADER_TIMEFRAME", "M5"),
                    "live": False,
                },
            },
        )
        assert conn_create.status_code == 201
        broker_connection_id = conn_create.json()["id"]

        bot_create = client.post(
            f"/v1/workspaces/{workspace_id}/bots",
            headers=headers,
            json={
                "name": "Smoke Live Bot",
                "symbol": os.getenv("CTRADER_SYMBOL", "EURUSD"),
                "timeframe": os.getenv("CTRADER_TIMEFRAME", "M5"),
                "mode": "live",
                "broker_connection_id": broker_connection_id,
            },
        )
        assert bot_create.status_code == 201
        bot_id = bot_create.json()["id"]

        started = client.post(
            f"/v1/workspaces/{workspace_id}/bots/{bot_id}/start",
            headers=headers,
            json={"reason": "smoke_live_start"},
        )
        assert started.status_code == 200, started.text

        readiness = client.get(
            f"/v1/workspaces/{workspace_id}/bots/{bot_id}/readiness",
            headers=headers,
        )
        assert readiness.status_code == 200
        readiness_payload = readiness.json()
        assert readiness_payload["bot_mode"] == "live"
        assert readiness_payload["runtime_mode"] == "running"
        assert readiness_payload["provider_mode"] == "live"
        assert readiness_payload["ready_for_live_trading"] is True

        tick_resp = client.post(
            f"/v1/workspaces/{workspace_id}/bots/{bot_id}/tick",
            headers=headers,
        )
        assert tick_resp.status_code == 200, tick_resp.text

        runtime_resp = client.get(
            f"/v1/workspaces/{workspace_id}/bots/{bot_id}/runtime",
            headers=headers,
        )
        assert runtime_resp.status_code == 200
        assert runtime_resp.json().get("status") in {"running", "paused"}

        stopped = client.post(
            f"/v1/workspaces/{workspace_id}/bots/{bot_id}/stop",
            headers=headers,
        )
        assert stopped.status_code == 200
