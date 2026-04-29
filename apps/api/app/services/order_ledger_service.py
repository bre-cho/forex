from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.order_projection_service import OrderProjectionService
from app.services.reconciliation_queue_service import ReconciliationQueueService
from app.services.safety_ledger import SafetyLedgerService


class OrderLedgerService:
    """Orchestrates full order lifecycle persistence and projection.

    Source-of-truth is maintained in ledger tables:
    - broker_order_attempts
    - order_state_transitions
    - broker_execution_receipts

    Projection is maintained in:
    - orders
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.ledger = SafetyLedgerService(db)
        self.projection = OrderProjectionService(db)
        self.recon_queue = ReconciliationQueueService(db)

    async def persist_intent(
        self,
        *,
        bot_instance_id: str,
        signal_id: str,
        brain_cycle_id: str | None,
        idempotency_key: str,
        broker: str,
        symbol: str,
        side: str,
        volume: float,
        request_payload: dict[str, Any],
        gate_context_hash: str | None = None,
    ) -> None:
        """Persist order intent before broker submit and update read-model projection."""
        attempt = await self.ledger.create_or_get_order_attempt(
            bot_instance_id=bot_instance_id,
            signal_id=signal_id,
            brain_cycle_id=brain_cycle_id,
            idempotency_key=idempotency_key,
            broker=broker,
            symbol=symbol,
            side=side,
            volume=volume,
            request_payload=request_payload,
            gate_context_hash=gate_context_hash,
            status="PENDING_SUBMIT",
        )
        await self.projection.upsert_from_order_attempt(bot_instance_id, attempt)

    async def persist_submit_requested(
        self,
        *,
        bot_instance_id: str,
        idempotency_key: str,
    ) -> None:
        """Mark attempt status as submit requested (state transition is handled elsewhere)."""
        await self.ledger.update_order_attempt(
            bot_instance_id=bot_instance_id,
            idempotency_key=idempotency_key,
            status="SUBMIT_REQUESTED",
        )

    async def persist_execution_receipt_and_projection(
        self,
        *,
        bot_instance_id: str,
        idempotency_key: str,
        broker: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """Persist broker receipt and update projection.

        Note: state transitions are validated and written by caller logic
        (`_record_transition_with_validation`).
        """
        mapped_status = {
            "order_filled": "FILLED",
            "order_rejected": "REJECTED",
            "order_unknown": "UNKNOWN",
        }[event_type]

        await self.ledger.record_execution_receipt(
            bot_instance_id=bot_instance_id,
            idempotency_key=idempotency_key,
            client_order_id=str(payload.get("client_order_id") or idempotency_key),
            broker=broker,
            broker_order_id=str(payload.get("broker_order_id") or "") or None,
            broker_position_id=str(payload.get("broker_position_id") or "") or None,
            broker_deal_id=str(payload.get("broker_deal_id") or "") or None,
            submit_status=str(payload.get("submit_status") or ("ACKED" if event_type == "order_filled" else "UNKNOWN")),
            fill_status=str(payload.get("fill_status") or mapped_status),
            requested_volume=float(payload.get("requested_volume") or payload.get("volume") or 0.0),
            filled_volume=float(payload.get("filled_volume") or payload.get("volume") or 0.0),
            avg_fill_price=float(payload.get("avg_fill_price") or payload.get("price") or 0.0) or None,
            commission=float(payload.get("commission") or 0.0),
            account_id=str(payload.get("account_id") or "") or None,
            server_time=float(payload.get("server_time")) if payload.get("server_time") is not None else None,
            latency_ms=float(payload.get("latency_ms") or 0.0),
            raw_response_hash=str(payload.get("raw_response_hash") or "") or None,
            raw_response=dict(payload.get("raw_response") or {}),
        )
        await self.ledger.update_order_attempt(
            bot_instance_id=bot_instance_id,
            idempotency_key=idempotency_key,
            status=mapped_status,
            broker_order_id=str(payload.get("broker_order_id") or "") or None,
            error_message=str(payload.get("error_message") or "") or None,
        )

        attempt = await self.ledger.get_order_attempt(bot_instance_id, idempotency_key)
        if attempt is not None:
            await self.projection.upsert_from_order_attempt(bot_instance_id, attempt)

        receipts = await self.ledger.list_execution_receipts(bot_instance_id, limit=50)
        for receipt in receipts:
            if str(receipt.idempotency_key or "") == idempotency_key:
                await self.projection.upsert_from_execution_receipt(bot_instance_id, receipt)
                break

    async def enqueue_unknown_order(
        self,
        *,
        bot_instance_id: str,
        idempotency_key: str,
        signal_id: str | None,
        payload: dict[str, Any],
    ) -> None:
        await self.recon_queue.enqueue_unknown_order(
            bot_instance_id=bot_instance_id,
            idempotency_key=idempotency_key,
            signal_id=signal_id,
            payload=payload,
        )
