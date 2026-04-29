from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.dependencies.auth import get_current_user
from app.models import User
from app.routers import qa_parity


def _user() -> User:
    return User(email="qa@example.com", hashed_password="x", full_name="QA")


@pytest.mark.asyncio
async def test_parity_check_and_audit_endpoint() -> None:
    app = FastAPI()
    app.include_router(qa_parity.router)

    async def _override_user() -> User:
        return _user()

    app.dependency_overrides[get_current_user] = _override_user

    payload = {
        "signal_id": "sig-1",
        "symbol": "EURUSD",
        "side": "BUY",
        "volume": 0.01,
        "order_type": "market",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        bad_demo = await client.post(
            "/v1/qa/parity-contract/check",
            json={"mode": "demo", "payload": payload},
        )
        assert bad_demo.status_code == 200
        assert bad_demo.json()["ok"] is False

        audit = await client.post(
            "/v1/qa/parity-contract/audit",
            json={
                "modes": ["backtest", "paper", "demo", "live"],
                "payload": {
                    **payload,
                    "idempotency_key": "idem-1",
                    "brain_cycle_id": "cycle-1",
                    "pre_execution_context": {"provider_mode": "live"},
                    "success": True,
                    "submit_status": "ACKED",
                    "fill_status": "FILLED",
                    "broker_order_id": "bo-1",
                },
            },
        )
        assert audit.status_code == 200
        data = audit.json()
        assert data["all_ok"] is True
        assert len(data["results"]) == 4
