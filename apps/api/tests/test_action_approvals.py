from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import token_revocation
from app.core.db import Base, get_db
from app.routers import action_approvals, auth, workspaces


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
async def test_action_approval_request_approve_and_list_flow(monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(workspaces.router)
    app.include_router(action_approvals.router)

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
        owner_tokens = await _register_and_login(client, "owner-approval@example.com")
        viewer_tokens = await _register_and_login(client, "viewer-approval@example.com")
        owner_headers = {"Authorization": f"Bearer {owner_tokens['access_token']}"}
        viewer_headers = {"Authorization": f"Bearer {viewer_tokens['access_token']}"}

        ws_resp = await client.post(
            "/v1/workspaces",
            headers=owner_headers,
            json={"name": "Approval WS", "slug": "approval-ws"},
        )
        assert ws_resp.status_code == 201
        workspace_id = ws_resp.json()["id"]

        add_member = await client.post(
            f"/v1/workspaces/{workspace_id}/members",
            headers=owner_headers,
            json={"email": "viewer-approval@example.com", "role": "viewer"},
        )
        assert add_member.status_code == 200

        req = await client.post(
            f"/v1/workspaces/{workspace_id}/approvals",
            headers=owner_headers,
            json={
                "action_type": "unlock_daily_lock",
                "bot_id": "bot-1",
                "reason": "operator unlock after incident",
            },
        )
        assert req.status_code == 200
        approval_id = int(req.json()["id"])
        assert req.json()["status"] == "pending"

        denied = await client.post(
            f"/v1/workspaces/{workspace_id}/approvals/{approval_id}/approve",
            headers=viewer_headers,
            json={"note": "try approve as viewer"},
        )
        assert denied.status_code == 403

        approved = await client.post(
            f"/v1/workspaces/{workspace_id}/approvals/{approval_id}/approve",
            headers=owner_headers,
            json={"note": "approved by owner"},
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "approved"

        listed = await client.get(
            f"/v1/workspaces/{workspace_id}/approvals",
            headers=owner_headers,
            params={"status": "approved"},
        )
        assert listed.status_code == 200
        assert any(int(item["id"]) == approval_id for item in listed.json())


@pytest.mark.asyncio
async def test_live_failover_reason_digest_helper_endpoint(monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(workspaces.router)
    app.include_router(action_approvals.router)

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
        owner_tokens = await _register_and_login(client, "owner-digest@example.com")
        viewer_tokens = await _register_and_login(client, "viewer-digest@example.com")
        owner_headers = {"Authorization": f"Bearer {owner_tokens['access_token']}"}
        viewer_headers = {"Authorization": f"Bearer {viewer_tokens['access_token']}"}

        ws_resp = await client.post(
            "/v1/workspaces",
            headers=owner_headers,
            json={"name": "Digest WS", "slug": "digest-ws"},
        )
        assert ws_resp.status_code == 201
        workspace_id = ws_resp.json()["id"]

        add_member = await client.post(
            f"/v1/workspaces/{workspace_id}/members",
            headers=owner_headers,
            json={"email": "viewer-digest@example.com", "role": "viewer"},
        )
        assert add_member.status_code == 200

        payload = {
            "bot_instance_id": "bot-live-1",
            "idempotency_key": "idem-123",
            "brain_cycle_id": "cycle-123",
            "signal_id": "signal-123",
            "symbol": "eurusd",
            "side": "BUY",
            "primary_provider": "CTRADER",
            "backup_providers": ["MT5", "  ctrader_backup  "],
        }

        denied = await client.post(
            f"/v1/workspaces/{workspace_id}/approvals/reason-digest/live-failover",
            headers=viewer_headers,
            json=payload,
        )
        assert denied.status_code == 403

        digest_resp_1 = await client.post(
            f"/v1/workspaces/{workspace_id}/approvals/reason-digest/live-failover",
            headers=owner_headers,
            json=payload,
        )
        assert digest_resp_1.status_code == 200
        body_1 = digest_resp_1.json()
        assert body_1["action_type"] == "live_provider_failover"
        assert len(str(body_1["reason_digest"])) == 64
        assert body_1["normalized_payload"]["symbol"] == "EURUSD"
        assert body_1["normalized_payload"]["side"] == "buy"
        assert body_1["normalized_payload"]["primary_provider"] == "ctrader"
        assert body_1["normalized_payload"]["backup_providers"] == ["ctrader_backup", "mt5"]

        digest_resp_2 = await client.post(
            f"/v1/workspaces/{workspace_id}/approvals/reason-digest/live-failover",
            headers=owner_headers,
            json=payload,
        )
        assert digest_resp_2.status_code == 200
        assert digest_resp_2.json()["reason_digest"] == body_1["reason_digest"]

        # suggested_request_payload must be ready-to-use for POST /approvals
        srp = body_1["suggested_request_payload"]
        assert srp["action_type"] == "live_provider_failover"
        assert srp["bot_id"] == "bot-live-1"
        assert srp["request_payload"]["reason_digest"] == body_1["reason_digest"]
        assert srp["request_payload"]["symbol"] == "EURUSD"
        assert srp["request_payload"]["primary_provider"] == "ctrader"
        assert srp["request_payload"]["backup_providers"] == ["ctrader_backup", "mt5"]
        assert "reason" in srp

        invalid_resp = await client.post(
            f"/v1/workspaces/{workspace_id}/approvals/reason-digest/live-failover",
            headers=owner_headers,
            json={"bot_instance_id": "bot-live-1"},
        )
        assert invalid_resp.status_code == 400
        assert "invalid_live_failover_reason_payload" in str(invalid_resp.json().get("detail") or "")
