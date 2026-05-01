from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.db import Base, get_db
from app.dependencies.auth import get_current_user
from app.models import (
    ActionApprovalRequest,
    BotInstance,
    BrokerAccountSnapshot,
    BrokerExecutionReceipt,
    BrokerReconciliationRun,
    DailyTradingState,
    ReconciliationAttemptEvent,
    ReconciliationQueueItem,
    StrategyExperiment,
    TradingIncident,
    User,
)
from app.routers import live_trading


class _FakeRuntime:
    def __init__(self) -> None:
        self.state = type("State", (), {"metadata": {}, "error_message": ""})()

    async def reconcile_now(self) -> dict:
        return {
            "bot_instance_id": "bot-1",
            "status": "ok",
            "open_positions_broker": 1,
            "open_positions_db": 1,
            "mismatches": [],
            "repaired": 0,
        }

    async def get_snapshot(self) -> dict:
        return {"status": "running", "metadata": {"market_data_ok": True}}


class _FakeRegistry:
    def __init__(self, runtime: _FakeRuntime, extra: dict[str, _FakeRuntime] | None = None):
        self._runtime = runtime
        self._extra = extra or {}

    def get(self, bot_id: str):
        if bot_id == "bot-1":
            return self._runtime
        return self._extra.get(bot_id)


def _build_user() -> User:
    return User(email="tester@example.com", hashed_password="hash", full_name="Tester", is_superuser=True)


@pytest.mark.asyncio
async def test_live_trading_action_endpoints() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = FastAPI()
    app.include_router(live_trading.router)
    app.state.registry = _FakeRegistry(_FakeRuntime())

    async def _override_get_db():
        async with session_maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def _override_user() -> User:
        return _build_user()

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_user

    async with session_maker() as session:
        session.add(
            BotInstance(
                id="bot-1",
                workspace_id="ws-1",
                name="Bot 1",
                symbol="EURUSD",
                timeframe="M5",
                mode="live",
                status="running",
            )
        )
        session.add(
            DailyTradingState(
                bot_instance_id="bot-1",
                trading_day=date.today(),
                locked=True,
                lock_reason="reconciliation_incident",
            )
        )
        session.add(
            BrokerReconciliationRun(
                bot_instance_id="bot-1",
                status="ok",
                open_positions_broker=1,
                open_positions_db=1,
                mismatches=[],
                repaired=0,
            )
        )
        session.add(
            TradingIncident(
                bot_instance_id="bot-1",
                incident_type="reconciliation_mismatch_persists",
                severity="critical",
                title="Mismatch",
                status="open",
            )
        )
        session.add(
            TradingIncident(
                bot_instance_id="bot-1",
                incident_type="daily_lock_controller_failure",
                severity="critical",
                title="Daily lock failed",
                status="open",
            )
        )
        session.add(
            BrokerAccountSnapshot(
                bot_instance_id="bot-1",
                broker="ctrader",
                account_id="acc-1",
                balance=1000.0,
                equity=1002.0,
                margin=20.0,
                free_margin=982.0,
                margin_level=5010.0,
                currency="USD",
                raw_response={},
            )
        )
        session.add(
            BrokerExecutionReceipt(
                bot_instance_id="bot-1",
                idempotency_key="idem-1",
                broker="ctrader",
                broker_order_id="ord-1",
                broker_position_id="pos-1",
                broker_deal_id="deal-1",
                submit_status="ACKED",
                fill_status="FILLED",
                requested_volume=0.01,
                filled_volume=0.01,
                avg_fill_price=1.2,
                commission=0.0,
                raw_response={},
            )
        )
        queue_item = ReconciliationQueueItem(
            bot_instance_id="bot-1",
            signal_id="sig-queue-1",
            idempotency_key="idem-queue-1",
            status="retry",
            attempts=1,
            max_attempts=3,
            payload={"reason": "unknown_order"},
        )
        session.add(queue_item)
        await session.flush()
        session.add(
            ReconciliationAttemptEvent(
                queue_item_id=queue_item.id,
                bot_instance_id="bot-1",
                signal_id="sig-queue-1",
                idempotency_key="idem-queue-1",
                worker_id="worker-test",
                attempt_no=1,
                outcome="not_found",
                resolution_code="not_found",
                provider="ctrader",
                payload_hash="hash-1",
                payload={"outcome": "not_found"},
            )
        )
        session.add(
            StrategyExperiment(
                bot_instance_id="bot-1",
                version=1,
                stage="DEMO_TEST",
                strategy_snapshot={"name": "wave"},
                policy_snapshot={"risk": 1},
                metrics_snapshot={"winrate": 0.55},
            )
        )
        session.add(ActionApprovalRequest(id=101, workspace_id="ws-1", bot_instance_id="bot-1", action_type="unlock_daily_lock", status="approved", reason="unlock-1"))
        session.add(ActionApprovalRequest(id=102, workspace_id="ws-1", bot_instance_id="bot-1", action_type="unlock_daily_lock", status="approved", reason="unlock-2"))
        session.add(ActionApprovalRequest(id=103, workspace_id="ws-1", bot_instance_id="bot-1", action_type="unlock_daily_lock", status="approved", reason="unlock-3"))
        session.add(ActionApprovalRequest(id=104, workspace_id="ws-1", bot_instance_id="bot-1", action_type="unlock_daily_lock", status="approved", reason="unlock-4"))
        session.add(ActionApprovalRequest(id=105, workspace_id="ws-1", bot_instance_id="bot-1", action_type="unlock_daily_lock", status="approved", reason="unlock-5"))
        session.add(ActionApprovalRequest(id=106, workspace_id="ws-1", bot_instance_id="bot-1", action_type="disable_kill_switch", status="approved", reason="unkill"))
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        rec = await client.post("/v1/workspaces/ws-1/bots/bot-1/reconcile-now")
        assert rec.status_code == 200
        assert rec.json()["status"] == "ok"

        rec_runs = await client.get("/v1/workspaces/ws-1/bots/bot-1/reconciliation-runs")
        assert rec_runs.status_code == 200
        assert rec_runs.json()

        queue_items = await client.get(
            "/v1/workspaces/ws-1/bots/bot-1/reconciliation/queue-items",
            params={"statuses": "dead_letter,failed_needs_operator,pending,retry", "limit": 50},
        )
        assert queue_items.status_code == 200
        assert isinstance(queue_items.json(), list)
        assert len(queue_items.json()) >= 1

        attempt_events = await client.get(
            "/v1/workspaces/ws-1/bots/bot-1/reconciliation/1/attempt-events",
            params={"limit": 50},
        )
        assert attempt_events.status_code == 200
        assert isinstance(attempt_events.json(), list)
        assert len(attempt_events.json()) == 1
        assert attempt_events.json()[0]["resolution_code"] == "not_found"

        receipts = await client.get("/v1/workspaces/ws-1/bots/bot-1/execution-receipts")
        assert receipts.status_code == 200

        ops = await client.get("/v1/workspaces/ws-1/bots/bot-1/operations-dashboard")
        assert ops.status_code == 200
        payload = ops.json()
        assert payload["runtime"]["status"] == "running"
        assert payload["latest_account_snapshot"]["currency"] == "USD"
        assert payload["latest_experiment"]["stage"] == "DEMO_TEST"

        incidents = await client.get("/v1/workspaces/ws-1/bots/bot-1/incidents")
        incident_id = next(
            item["id"]
            for item in incidents.json()
            if item.get("incident_type") == "reconciliation_mismatch_persists"
        )

        res = await client.post(f"/v1/workspaces/ws-1/bots/bot-1/incidents/{incident_id}/resolve")
        assert res.status_code == 200
        assert res.json()["status"] == "resolved"

        reset = await client.post(
            "/v1/workspaces/ws-1/bots/bot-1/daily-state/reset-lock",
            json={"reason": "incident_resolved", "approval_id": 101},
        )
        assert reset.status_code == 409
        assert reset.json()["detail"] == "operator_ack_required_for_open_critical_daily_lock_incident"

        reset_with_ack = await client.post(
            "/v1/workspaces/ws-1/bots/bot-1/daily-state/reset-lock",
            json={
                "reason": "incident_resolved",
                "approval_id": 102,
                "acknowledge_operator_action": True,
            },
        )
        assert reset_with_ack.status_code == 200
        assert reset_with_ack.json()["locked"] is False

        bad_reset = await client.post(
            "/v1/workspaces/ws-1/bots/bot-1/daily-state/reset-lock",
            json={"approval_id": 103},
        )
        assert bad_reset.status_code == 400

        blocked_reset = await client.post(
            "/v1/workspaces/ws-1/bots/bot-1/daily-state/reset-lock",
            json={"reason": "operator_reset_no_ack", "scope": "bot", "approval_id": 104},
        )
        assert blocked_reset.status_code == 409

        reset_ack = await client.post(
            "/v1/workspaces/ws-1/bots/bot-1/daily-state/reset-lock",
            json={
                "reason": "operator_reset_with_ack",
                "scope": "bot",
                "approval_id": 105,
                "acknowledge_operator_action": True,
            },
        )
        assert reset_ack.status_code == 200
        assert reset_ack.json()["scope"] == "bot"

        kill = await client.post(
            "/v1/workspaces/ws-1/bots/bot-1/kill-switch",
            json={"reason": "operator_emergency_stop"},
        )
        assert kill.status_code == 200
        assert kill.json()["kill_switch"] is True

        unkill = await client.post(
            "/v1/workspaces/ws-1/bots/bot-1/reset-kill-switch",
            json={"reason": "incident_cleared", "approval_id": 106},
        )
        assert unkill.status_code == 200
        assert unkill.json()["kill_switch"] is False


@pytest.mark.asyncio
async def test_reset_daily_lock_portfolio_scope_requires_ack_and_unlocks_workspace() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = FastAPI()
    app.include_router(live_trading.router)
    app.state.registry = _FakeRegistry(_FakeRuntime(), extra={"bot-2": _FakeRuntime()})

    async def _override_get_db():
        async with session_maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def _override_user() -> User:
        return _build_user()

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_user

    async with session_maker() as session:
        session.add(
            BotInstance(
                id="bot-1",
                workspace_id="ws-1",
                name="Bot 1",
                symbol="EURUSD",
                timeframe="M5",
                mode="live",
                status="running",
            )
        )
        session.add(
            BotInstance(
                id="bot-2",
                workspace_id="ws-1",
                name="Bot 2",
                symbol="GBPUSD",
                timeframe="M5",
                mode="live",
                status="running",
            )
        )
        session.add(
            DailyTradingState(
                bot_instance_id="bot-1",
                trading_day=date.today(),
                locked=True,
                lock_reason="daily_loss_limit_reached",
            )
        )
        session.add(
            DailyTradingState(
                bot_instance_id="bot-2",
                trading_day=date.today(),
                locked=True,
                lock_reason="daily_loss_limit_reached",
            )
        )
        session.add(
            TradingIncident(
                bot_instance_id="bot-2",
                incident_type="daily_lock_controller_failure",
                severity="critical",
                title="Daily lock failed",
                status="open",
            )
        )
        session.add(ActionApprovalRequest(id=201, workspace_id="ws-1", bot_instance_id="bot-1", action_type="unlock_daily_lock", status="approved", reason="portfolio-unlock-1"))
        session.add(ActionApprovalRequest(id=202, workspace_id="ws-1", bot_instance_id="bot-1", action_type="unlock_daily_lock", status="approved", reason="portfolio-unlock-2"))
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        blocked = await client.post(
            "/v1/workspaces/ws-1/bots/bot-1/daily-state/reset-lock",
            json={"reason": "operator_portfolio_reset_no_ack", "scope": "portfolio", "approval_id": 201},
        )
        assert blocked.status_code == 409
        assert blocked.json()["detail"] == "operator_ack_required_for_open_critical_daily_lock_incident"

        allowed = await client.post(
            "/v1/workspaces/ws-1/bots/bot-1/daily-state/reset-lock",
            json={
                "reason": "operator_portfolio_reset",
                "scope": "portfolio",
                "approval_id": 202,
                "acknowledge_operator_action": True,
            },
        )
        assert allowed.status_code == 200
        payload = allowed.json()
        assert payload["scope"] == "portfolio"
        assert sorted(payload["affected_bots"]) == ["bot-1", "bot-2"]
        assert payload["locked"] is False

    async with session_maker() as session:
        states = (
            (
                await session.execute(
                    DailyTradingState.__table__.select().where(
                        DailyTradingState.bot_instance_id.in_(["bot-1", "bot-2"]),
                        DailyTradingState.trading_day == date.today(),
                    )
                )
            )
            .mappings()
            .all()
        )
        by_bot = {str(row["bot_instance_id"]): row for row in states}
        assert bool(by_bot["bot-1"]["locked"]) is False
        assert bool(by_bot["bot-2"]["locked"]) is False


@pytest.mark.asyncio
async def test_manual_reconciliation_requires_structured_broker_proof() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = FastAPI()
    app.include_router(live_trading.router)
    app.state.registry = _FakeRegistry(_FakeRuntime())

    async def _override_get_db():
        async with session_maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def _override_user() -> User:
        return _build_user()

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_user

    async with session_maker() as session:
        session.add(
            BotInstance(
                id="bot-1",
                workspace_id="ws-1",
                name="Bot 1",
                symbol="EURUSD",
                timeframe="M5",
                mode="live",
                status="running",
            )
        )
        session.add(
            ReconciliationQueueItem(
                bot_instance_id="bot-1",
                signal_id="sig-1",
                idempotency_key="idem-unknown-1",
                status="dead_letter",
                attempts=3,
                max_attempts=3,
                payload={"reason": "manual_resolution_required"},
            )
        )
        session.add(ActionApprovalRequest(id=301, workspace_id="ws-1", bot_instance_id="bot-1", action_type="retry_unknown_order", status="approved", reason="retry-1"))
        session.add(ActionApprovalRequest(id=302, workspace_id="ws-1", bot_instance_id="bot-1", action_type="retry_unknown_order", status="approved", reason="retry-2"))
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        bad = await client.post(
            "/v1/workspaces/ws-1/bots/bot-1/reconciliation/1/resolve",
            json={
                "approval_id": 301,
                "outcome": "filled",
                "broker_proof": {"provider": "ctrader"},
            },
        )
        assert bad.status_code == 400
        assert "broker_proof_invalid_missing_fields" in bad.json()["detail"]

        with patch(
            "app.services.order_ledger_service.OrderLedgerService.record_lifecycle_event",
            new=AsyncMock(return_value=None),
        ):
            good = await client.post(
                "/v1/workspaces/ws-1/bots/bot-1/reconciliation/1/resolve",
                json={
                    "approval_id": 302,
                    "outcome": "filled",
                    "broker_proof": {
                        "provider": "ctrader",
                        "evidence_ref": "ticket-123",
                        "observed_at": "2026-04-30T10:10:10Z",
                        "raw_response_hash": "abc123",
                        "broker_order_id": "ord-777",
                    },
                },
            )
        assert good.status_code == 200
        assert good.json()["status"] == "resolved"
