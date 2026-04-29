from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.order_ledger_service import OrderLedgerService


@pytest.mark.asyncio
async def test_persist_intent_creates_attempt_and_projection() -> None:
    svc = OrderLedgerService(MagicMock())
    attempt = MagicMock()
    svc.ledger = MagicMock()
    svc.projection = MagicMock()
    svc.recon_queue = MagicMock()
    svc.ledger.create_or_get_order_attempt = AsyncMock(return_value=attempt)
    svc.projection.upsert_from_order_attempt = AsyncMock()

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

    svc.ledger.create_or_get_order_attempt.assert_awaited_once()
    svc.projection.upsert_from_order_attempt.assert_awaited_once_with("bot-1", attempt)


@pytest.mark.asyncio
async def test_persist_execution_receipt_unknown_enqueues_reconciliation() -> None:
    svc = OrderLedgerService(MagicMock())
    svc.ledger = MagicMock()
    svc.projection = MagicMock()
    svc.recon_queue = MagicMock()

    svc.ledger.record_execution_receipt = AsyncMock()
    svc.ledger.update_order_attempt = AsyncMock()
    svc.ledger.record_order_state_transition = AsyncMock()
    svc.ledger.get_order_attempt = AsyncMock(return_value=MagicMock())
    mock_receipt = MagicMock()
    mock_receipt.idempotency_key = "idem-2"
    svc.ledger.list_execution_receipts = AsyncMock(return_value=[mock_receipt])

    svc.projection.upsert_from_order_attempt = AsyncMock()
    svc.projection.upsert_from_execution_receipt = AsyncMock()
    svc.recon_queue.enqueue_unknown_order = AsyncMock()

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

    svc.ledger.record_execution_receipt.assert_awaited_once()
    svc.recon_queue.enqueue_unknown_order.assert_not_awaited()

    await svc.enqueue_unknown_order(
        bot_instance_id="bot-2",
        idempotency_key="idem-2",
        signal_id="sig-2",
        payload=payload,
    )
    svc.recon_queue.enqueue_unknown_order.assert_awaited_once()
