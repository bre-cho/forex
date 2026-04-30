from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    BrokerExecutionReceipt,
    BrokerOrderAttempt,
    Order,
    OrderStateTransition,
    ReconciliationQueueItem,
    SubmitOutbox,
)
from app.services.order_ledger_integrity_service import OrderLedgerIntegrityService


@pytest.mark.asyncio
async def test_integrity_detects_state_projection_mismatch() -> None:
    pytest.importorskip("aiosqlite")

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Order.__table__.create)
        await conn.run_sync(OrderStateTransition.__table__.create)
        await conn.run_sync(BrokerOrderAttempt.__table__.create)
        await conn.run_sync(BrokerExecutionReceipt.__table__.create)
        await conn.run_sync(ReconciliationQueueItem.__table__.create)
        await conn.run_sync(SubmitOutbox.__table__.create)

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        db.add(
            Order(
                bot_instance_id="bot-1",
                idempotency_key="idem-1",
                symbol="EURUSD",
                side="buy",
                order_type="market",
                volume=0.1,
                status="submitted",
                current_state="SUBMITTED",
            )
        )
        db.add(
            OrderStateTransition(
                bot_instance_id="bot-1",
                signal_id="sig-1",
                idempotency_key="idem-1",
                from_state="SUBMITTED",
                to_state="FILLED",
                event_type="order_filled",
                detail="ok",
                payload={},
            )
        )
        await db.commit()

        svc = OrderLedgerIntegrityService(db)
        report = await svc.run()
        assert report["ok"] is False
        codes = {i["code"] for i in report["issues"]}
        assert "order_state_projection_mismatch" in codes

    await engine.dispose()


@pytest.mark.asyncio
async def test_integrity_detects_unknown_after_send_without_queue() -> None:
    pytest.importorskip("aiosqlite")

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Order.__table__.create)
        await conn.run_sync(OrderStateTransition.__table__.create)
        await conn.run_sync(BrokerOrderAttempt.__table__.create)
        await conn.run_sync(BrokerExecutionReceipt.__table__.create)
        await conn.run_sync(ReconciliationQueueItem.__table__.create)
        await conn.run_sync(SubmitOutbox.__table__.create)

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        db.add(
            SubmitOutbox(
                bot_instance_id="bot-2",
                idempotency_key="idem-2",
                phase="UNKNOWN_AFTER_SEND",
                request_hash="h",
                provider="ctrader",
                phase_payload={},
            )
        )
        await db.commit()

        svc = OrderLedgerIntegrityService(db)
        report = await svc.run()
        codes = {i["code"] for i in report["issues"]}
        assert "submit_outbox_unknown_after_send_without_queue" in codes

    await engine.dispose()
