from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import SubmitOutbox, SubmitOutboxEvent
from app.services.submit_outbox_service import SubmitOutboxService


@pytest.mark.asyncio
async def test_submit_outbox_mark_phase_upserts_row() -> None:
    pytest.importorskip("aiosqlite")

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SubmitOutbox.__table__.create)
        await conn.run_sync(SubmitOutboxEvent.__table__.create)

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        svc = SubmitOutboxService(db)

        row1 = await svc.mark_phase(
            bot_instance_id="bot-1",
            idempotency_key="idem-1",
            phase="BROKER_SEND_STARTED",
            request_hash="hash-1",
            provider="ctrader",
            phase_payload={"signal_id": "sig-1"},
        )
        assert row1.phase == "BROKER_SEND_STARTED"

        row2 = await svc.mark_phase(
            bot_instance_id="bot-1",
            idempotency_key="idem-1",
            phase="BROKER_SEND_RETURNED",
            request_hash="hash-1",
            provider="ctrader",
            phase_payload={"submit_status": "ACKED"},
        )
        assert row2.phase == "BROKER_SEND_RETURNED"

        rows = (await db.execute(select(SubmitOutbox))).scalars().all()
        assert len(rows) == 1
        assert rows[0].idempotency_key == "idem-1"

        events = (await db.execute(select(SubmitOutboxEvent))).scalars().all()
        assert len(events) == 2
        assert events[0].phase == "BROKER_SEND_STARTED"
        assert events[1].phase == "BROKER_SEND_RETURNED"

    await engine.dispose()
