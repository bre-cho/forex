from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import token_revocation
from app.core.db import Base, get_db
from app.events import publishers
from app.routers import auth, bots, signals, workspaces, ws
from app.services import bot_service


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
        cursor = 0
        while True:
            while cursor < len(self._redis.published):
                channel, payload = self._redis.published[cursor]
                cursor += 1
                if channel in self._channels:
                    yield {"type": "message", "data": payload}
            await asyncio.sleep(0.01)


async def _create_all(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def test_paper_runtime_e2e_api_and_ws_assertions(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    asyncio.run(_create_all(engine))

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(workspaces.router)
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
                "email": "paper-e2e@example.com",
                "password": "StrongPass123!",
                "full_name": "Paper E2E",
            },
        )
        assert register.status_code == 201

        login = client.post(
            "/v1/auth/login",
            json={"email": "paper-e2e@example.com", "password": "StrongPass123!"},
        )
        assert login.status_code == 200
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        ws_create = client.post(
            "/v1/workspaces",
            headers=headers,
            json={"name": "E2E", "slug": "e2e-paper-ws"},
        )
        assert ws_create.status_code == 201
        workspace_id = ws_create.json()["id"]

        bot_create = client.post(
            f"/v1/workspaces/{workspace_id}/bots",
            headers=headers,
            json={"name": "Paper Bot", "symbol": "EURUSD", "timeframe": "M5", "mode": "paper"},
        )
        assert bot_create.status_code == 201
        bot_id = bot_create.json()["id"]

        started = client.post(
            f"/v1/workspaces/{workspace_id}/bots/{bot_id}/start",
            headers=headers,
        )
        assert started.status_code == 200

        with client.websocket_connect(f"/ws/bots/{bot_id}?token={token}") as ws_client:
            # Drive runtime via API endpoints only (no private runtime method usage).
            tick_resp = client.post(
                f"/v1/workspaces/{workspace_id}/bots/{bot_id}/tick",
                headers=headers,
            )
            assert tick_resp.status_code == 200

            manual_signal_resp = client.post(
                f"/v1/workspaces/{workspace_id}/bots/{bot_id}/manual-signal",
                headers=headers,
                params={"direction": "BUY", "confidence": 0.99},
            )
            assert manual_signal_resp.status_code == 200

            signals_resp = client.get(
                f"/v1/workspaces/{workspace_id}/bots/{bot_id}/signals",
                headers=headers,
            )
            orders_resp = client.get(
                f"/v1/workspaces/{workspace_id}/bots/{bot_id}/orders",
                headers=headers,
            )
            trades_resp = client.get(
                f"/v1/workspaces/{workspace_id}/bots/{bot_id}/trades",
                headers=headers,
            )
            snapshots_resp = client.get(
                f"/v1/workspaces/{workspace_id}/bots/{bot_id}/snapshots",
                headers=headers,
            )

            assert signals_resp.status_code == 200
            assert orders_resp.status_code == 200
            assert trades_resp.status_code == 200
            assert snapshots_resp.status_code == 200

            assert len(signals_resp.json()) >= 1
            assert len(orders_resp.json()) >= 1
            assert len(trades_resp.json()) >= 1
            assert len(snapshots_resp.json()) >= 1

            open_trade = trades_resp.json()[0]
            position_id = open_trade["broker_trade_id"]

            close_resp = client.post(
                f"/v1/workspaces/{workspace_id}/bots/{bot_id}/positions/{position_id}/close",
                headers=headers,
            )
            assert close_resp.status_code == 200

            updated_trades_resp = client.get(
                f"/v1/workspaces/{workspace_id}/bots/{bot_id}/trades",
                headers=headers,
            )
            assert updated_trades_resp.status_code == 200
            updated_trades = updated_trades_resp.json()
            target = next(t for t in updated_trades if t["broker_trade_id"] == position_id)
            assert target["status"] == "closed"
            assert target["closed_at"] is not None

            # Assert WS delivers lifecycle events forwarded from Redis channels.
            received_event_types: set[str] = set()
            required_events = {"signal_generated", "trade_closed"}
            for _ in range(20):
                packet = json.loads(ws_client.receive_text())
                if packet.get("event") != "message":
                    continue
                event_type = packet.get("payload", {}).get("event_type")
                if event_type:
                    received_event_types.add(str(event_type))
                if required_events.issubset(received_event_types):
                    break

            assert "signal_generated" in received_event_types
            assert "trade_closed" in received_event_types

        stopped = client.post(
            f"/v1/workspaces/{workspace_id}/bots/{bot_id}/stop",
            headers=headers,
        )
        assert stopped.status_code == 200
