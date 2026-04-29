from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import BrokerExecutionReceipt, BrokerOrderAttempt
from app.services.order_ledger_service import OrderLedgerService


@pytest.mark.asyncio
async def test_crash_after_submit_rolls_back_broker_result_transaction() -> None:
    pytest.importorskip("aiosqlite")

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BrokerOrderAttempt.__table__.create)
        await conn.run_sync(BrokerExecutionReceipt.__table__.create)

    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        svc = OrderLedgerService(db)

        # Avoid projection table dependency in this focused integration test.
        svc.projection.upsert_from_order_attempt = AsyncMock(return_value=None)
        svc.projection.upsert_from_execution_receipt = AsyncMock(return_value=None)

        await svc.uow.reserve_intent(
            bot_instance_id="bot-1",
            signal_id="sig-1",
            brain_cycle_id="cycle-1",
            idempotency_key="idem-1",
            broker="ctrader",
            symbol="EURUSD",
            side="buy",
            volume=0.1,
            request_payload={"symbol": "EURUSD", "side": "buy", "volume": 0.1},
            gate_context_hash="gate-hash-1",
        )
        await svc.uow.mark_submitting_atomic(bot_instance_id="bot-1", idempotency_key="idem-1")

        async def _crash_projection(*args, **kwargs):
            raise RuntimeError("simulated_crash_after_submit")

        svc.projection.upsert_from_order_attempt = _crash_projection

        with pytest.raises(RuntimeError, match="simulated_crash_after_submit"):
            await svc.uow.persist_broker_result_atomic(
                bot_instance_id="bot-1",
                idempotency_key="idem-1",
                broker="ctrader",
                event_type="order_unknown",
                payload={
                    "client_order_id": "idem-1",
                    "volume": 0.1,
                    "fill_status": "UNKNOWN",
                    "submit_status": "UNKNOWN",
                    "raw_response": {"timeout": True},
                },
            )

        await db.rollback()

        attempt = (
            (
                await db.execute(
                    select(BrokerOrderAttempt).where(
                        BrokerOrderAttempt.bot_instance_id == "bot-1",
                        BrokerOrderAttempt.idempotency_key == "idem-1",
                    )
                )
            )
            .scalars()
            .one()
        )
        assert str(attempt.status) == "SUBMIT_REQUESTED"
        assert str(attempt.current_state) == "SUBMITTED"

        receipts = (await db.execute(select(BrokerExecutionReceipt))).scalars().all()
        assert receipts == []

    await engine.dispose()
