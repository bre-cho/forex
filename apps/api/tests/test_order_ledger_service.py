from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.order_ledger_service import OrderLedgerService


@pytest.mark.asyncio
async def test_persist_intent_creates_attempt_and_projection() -> None:
    svc = OrderLedgerService(MagicMock())
    svc.uow = MagicMock()
    svc.uow.reserve_intent = AsyncMock()

    await svc.persist_intent(
        bot_instance_id="bot-1",
        signal_id="sig-1",
        brain_cycle_id="cycle-1",
        idempotency_key="idem-1",
        broker="ctrader",
        symbol="EURUSD",
        side="BUY",
        volume=0.1,
        request_payload={"order_type": "market"},
    )

    svc.uow.reserve_intent.assert_awaited_once()


@pytest.mark.asyncio
async def test_persist_execution_receipt_unknown_enqueues_reconciliation() -> None:
    svc = OrderLedgerService(MagicMock())
    svc.uow = MagicMock()
    svc.uow.persist_broker_result_atomic = AsyncMock()
    svc.uow.enqueue_unknown_atomic = AsyncMock()

    payload = {
        "broker_order_id": "ord-1",
        "volume": 0.2,
        "price": 1.1,
        "raw_response": {},
    }
    await svc.persist_execution_receipt_and_projection(
        bot_instance_id="bot-2",
        idempotency_key="idem-2",
        broker="ctrader",
        event_type="order_unknown",
        payload=payload,
    )

    svc.uow.persist_broker_result_atomic.assert_awaited_once()

    await svc.enqueue_unknown_order(
        bot_instance_id="bot-2",
        idempotency_key="idem-2",
        signal_id="sig-2",
        payload=payload,
    )
    svc.uow.enqueue_unknown_atomic.assert_awaited_once()
