from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.db import Base
from app.services.safety_ledger import SafetyLedgerService


@pytest.mark.asyncio
async def test_record_and_list_order_state_transitions() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_maker() as session:
        svc = SafetyLedgerService(session)

        await svc.record_order_state_transition(
            bot_instance_id="bot-1",
            signal_id="sig-1",
            idempotency_key="idem-1",
            from_state="intent_created",
            to_state="gate_allowed",
            event_type="gate_evaluated",
            detail="ok",
            payload={"gate_action": "ALLOW"},
        )

        await svc.record_order_state_transition(
            bot_instance_id="bot-1",
            signal_id="sig-1",
            idempotency_key="idem-1",
            from_state="gate_allowed",
            to_state="submitted",
            event_type="order_submitted",
            detail="broker_submit_requested",
            payload={},
        )

        rows = await svc.list_order_state_transitions("bot-1", limit=10)
        assert len(rows) == 2
        assert rows[0].to_state in {"submitted", "gate_allowed"}
