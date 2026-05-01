from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.db import Base, get_db
from app.dependencies.auth import get_current_user
from app.models import BotInstance, DailyTradingState, User, Workspace
from app.routers import admin


def _admin_user() -> User:
    return User(email="root@example.com", hashed_password="hash", full_name="Root", is_superuser=True)


@pytest.mark.asyncio
async def test_dr_snapshot_and_restore_dry_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DR_SNAPSHOT_DIR", str(tmp_path / "dr"))
    monkeypatch.setenv("DR_SNAPSHOT_SIGNING_KEY", "unit-test-dr-key")

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = FastAPI()
    app.include_router(admin.router)

    async def _override_get_db():
        async with session_maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def _override_user() -> User:
        return _admin_user()

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_user

    async with session_maker() as session:
        session.add(
            Workspace(
                id="ws-1",
                name="WS",
                slug="ws",
                owner_id="owner-1",
                settings={"portfolio_new_orders_paused": True},
            )
        )
        session.add(
            BotInstance(
                id="bot-1",
                workspace_id="ws-1",
                name="Bot 1",
                symbol="EURUSD",
                timeframe="M5",
                mode="live",
                status="error",
            )
        )
        session.add(
            DailyTradingState(
                bot_instance_id="bot-1",
                trading_day=date.today(),
                locked=True,
            )
        )
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_resp = await client.post("/v1/admin/dr/snapshot", json={"include_runtime": True})
        assert create_resp.status_code == 200
        payload = create_resp.json()
        assert payload["status"] == "created"
        assert payload["signed"] is True
        snapshot_id = str(payload["snapshot_id"])

        list_resp = await client.get("/v1/admin/dr/snapshots")
        assert list_resp.status_code == 200
        assert any(item["snapshot_id"] == snapshot_id for item in list_resp.json()["snapshots"])

        restore_resp = await client.post(
            f"/v1/admin/dr/restore/{snapshot_id}",
            json={"dry_run": True},
        )
        assert restore_resp.status_code == 200
        restore_payload = restore_resp.json()
        assert restore_payload["status"] == "ok"
        assert restore_payload["dry_run"] is True
        assert int(restore_payload["planned"]["workspace_pause_updates"]) >= 1


@pytest.mark.asyncio
async def test_dr_restore_rejects_tampered_snapshot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DR_SNAPSHOT_DIR", str(tmp_path / "dr"))
    monkeypatch.setenv("DR_SNAPSHOT_SIGNING_KEY", "unit-test-dr-key")

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = FastAPI()
    app.include_router(admin.router)

    async def _override_get_db():
        async with session_maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def _override_user() -> User:
        return _admin_user()

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_user

    async with session_maker() as session:
        session.add(
            Workspace(
                id="ws-2",
                name="WS 2",
                slug="ws-2",
                owner_id="owner-2",
                settings={"portfolio_new_orders_paused": False},
            )
        )
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_resp = await client.post("/v1/admin/dr/snapshot", json={"include_runtime": True})
        assert create_resp.status_code == 200
        payload = create_resp.json()
        snapshot_id = str(payload["snapshot_id"])
        snapshot_path = Path(str(payload["path"]))

        raw = json.loads(snapshot_path.read_text(encoding="utf-8"))
        raw_payload = dict(raw.get("payload") or {})
        counts = dict(raw_payload.get("counts") or {})
        counts["workspaces"] = int(counts.get("workspaces", 0)) + 1
        raw_payload["counts"] = counts
        raw["payload"] = raw_payload
        snapshot_path.write_text(json.dumps(raw, ensure_ascii=True, separators=(",", ":")), encoding="utf-8")

        restore_resp = await client.post(
            f"/v1/admin/dr/restore/{snapshot_id}",
            json={"dry_run": True},
        )
        assert restore_resp.status_code == 422
        assert "snapshot_integrity_check_failed" in str(restore_resp.json().get("detail") or "")
