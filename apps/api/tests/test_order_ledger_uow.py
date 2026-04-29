from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.order_ledger_service import OrderLifecycleUnitOfWork


class _Tx:
    def __init__(self):
        self.exited_with_error = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.exited_with_error = exc_type is not None
        return False


@pytest.mark.asyncio
async def test_uow_reserve_intent_uses_single_transaction_no_autocommit() -> None:
    db = MagicMock()
    tx = _Tx()
    db.begin = MagicMock(return_value=tx)

    ledger = MagicMock()
    projection = MagicMock()
    recon = MagicMock()

    attempt = MagicMock()
    ledger.create_or_get_order_attempt = AsyncMock(return_value=attempt)
    projection.upsert_from_order_attempt = AsyncMock()

    uow = OrderLifecycleUnitOfWork(db=db, ledger=ledger, projection=projection, recon_queue=recon)
    await uow.reserve_intent(
        bot_instance_id="bot-1",
        signal_id="sig-1",
        brain_cycle_id="cycle-1",
        idempotency_key="idem-1",
        broker="ctrader",
        symbol="EURUSD",
        side="buy",
        volume=0.1,
        request_payload={"a": 1},
        gate_context_hash="h1",
    )

    db.begin.assert_called_once()
    ledger.create_or_get_order_attempt.assert_awaited_once()
    assert ledger.create_or_get_order_attempt.await_args.kwargs["auto_commit"] is False
    projection.upsert_from_order_attempt.assert_awaited_once_with("bot-1", attempt, auto_commit=False)


@pytest.mark.asyncio
async def test_uow_persist_broker_result_marks_tx_error_on_failure() -> None:
    db = MagicMock()
    tx = _Tx()
    db.begin = MagicMock(return_value=tx)

    ledger = MagicMock()
    projection = MagicMock()
    recon = MagicMock()

    ledger.record_execution_receipt = AsyncMock(return_value=MagicMock())
    ledger.update_order_attempt = AsyncMock(return_value=MagicMock())
    projection.upsert_from_order_attempt = AsyncMock(side_effect=RuntimeError("projection_down"))
    projection.upsert_from_execution_receipt = AsyncMock()

    uow = OrderLifecycleUnitOfWork(db=db, ledger=ledger, projection=projection, recon_queue=recon)

    with pytest.raises(RuntimeError, match="projection_down"):
        await uow.persist_broker_result_atomic(
            bot_instance_id="bot-1",
            idempotency_key="idem-1",
            broker="ctrader",
            event_type="order_unknown",
            payload={"volume": 0.1},
        )

    assert tx.exited_with_error is True
