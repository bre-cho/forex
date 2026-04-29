from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.order_projection_service import OrderProjectionService
from app.services.reconciliation_queue_service import ReconciliationQueueService
from app.services.safety_ledger import SafetyLedgerService


@dataclass
class OrderLifecycleUnitOfWork:
    db: AsyncSession
    ledger: SafetyLedgerService
    projection: OrderProjectionService
    recon_queue: ReconciliationQueueService

    async def reserve_intent(
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
        async with self.db.begin():
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
                auto_commit=False,
            )
            await self.projection.upsert_from_order_attempt(bot_instance_id, attempt, auto_commit=False)

    async def mark_submitting_atomic(
        self,
        *,
        bot_instance_id: str,
        idempotency_key: str,
    ) -> None:
        async with self.db.begin():
            row = await self.ledger.update_order_attempt(
                bot_instance_id=bot_instance_id,
                idempotency_key=idempotency_key,
                status="SUBMIT_REQUESTED",
                current_state="SUBMITTED",
                auto_commit=False,
            )
            if row is None:
                raise ValueError("order_attempt_not_found")

    async def persist_broker_result_atomic(
        self,
        *,
        bot_instance_id: str,
        idempotency_key: str,
        broker: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        mapped_status = {
            "order_filled": "FILLED",
            "order_rejected": "REJECTED",
            "order_unknown": "UNKNOWN",
        }[event_type]

        async with self.db.begin():
            receipt = await self.ledger.record_execution_receipt(
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
                auto_commit=False,
            )
            attempt = await self.ledger.update_order_attempt(
                bot_instance_id=bot_instance_id,
                idempotency_key=idempotency_key,
                status=mapped_status,
                current_state=mapped_status,
                broker_order_id=str(payload.get("broker_order_id") or "") or None,
                error_message=str(payload.get("error_message") or "") or None,
                auto_commit=False,
            )
            if attempt is not None:
                await self.projection.upsert_from_order_attempt(bot_instance_id, attempt, auto_commit=False)
            await self.projection.upsert_from_execution_receipt(bot_instance_id, receipt, auto_commit=False)

    async def enqueue_unknown_atomic(
        self,
        *,
        bot_instance_id: str,
        idempotency_key: str,
        signal_id: str | None,
        payload: dict[str, Any],
    ) -> None:
        async with self.db.begin():
            await self.recon_queue.enqueue_unknown_order(
                bot_instance_id=bot_instance_id,
                idempotency_key=idempotency_key,
                signal_id=signal_id,
                payload=payload,
                auto_commit=False,
            )


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
        self.uow = OrderLifecycleUnitOfWork(
            db=db,
            ledger=self.ledger,
            projection=self.projection,
            recon_queue=self.recon_queue,
        )

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
        await self.uow.reserve_intent(
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
        )

    async def persist_submit_requested(
        self,
        *,
        bot_instance_id: str,
        idempotency_key: str,
    ) -> None:
        """Mark attempt status as submit requested (state transition is handled elsewhere)."""
        await self.uow.mark_submitting_atomic(
            bot_instance_id=bot_instance_id,
            idempotency_key=idempotency_key,
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
        await self.uow.persist_broker_result_atomic(
            bot_instance_id=bot_instance_id,
            idempotency_key=idempotency_key,
            broker=broker,
            event_type=event_type,
            payload=payload,
        )

    async def enqueue_unknown_order(
        self,
        *,
        bot_instance_id: str,
        idempotency_key: str,
        signal_id: str | None,
        payload: dict[str, Any],
    ) -> None:
        await self.uow.enqueue_unknown_atomic(
            bot_instance_id=bot_instance_id,
            idempotency_key=idempotency_key,
            signal_id=signal_id,
            payload=payload,
        )

    async def record_lifecycle_event(
        self,
        *,
        bot_instance_id: str,
        event_type: str,
        idempotency_key: str,
        broker: str,
        payload: dict[str, Any],
    ) -> None:
        """P0.5: Atomic lifecycle event — persist receipt, update attempt, projection, and
        enqueue reconciliation (for UNKNOWN) in a single coordination call.

        event_type: order_submitted | order_unknown | order_rejected | order_filled
        """
        import hashlib, json as _json
        # Server-side hash of raw_response if not provided
        if payload.get("raw_response") and not payload.get("raw_response_hash"):
            raw = payload["raw_response"]
            payload["raw_response_hash"] = hashlib.sha256(
                _json.dumps(raw, sort_keys=True, default=str).encode()
            ).hexdigest()

        if event_type == "order_submitted":
            await self.persist_intent(
                bot_instance_id=bot_instance_id,
                signal_id=str(payload.get("signal_id") or idempotency_key),
                brain_cycle_id=str(payload.get("brain_cycle_id") or "") or None,
                idempotency_key=idempotency_key,
                broker=broker,
                symbol=str(payload.get("symbol") or ""),
                side=str(payload.get("side") or ""),
                volume=float(payload.get("volume") or 0.0),
                request_payload=payload,
                gate_context_hash=str(payload.get("gate_context_hash") or "") or None,
            )
        elif event_type in {"order_filled", "order_rejected", "order_unknown"}:
            await self.persist_execution_receipt_and_projection(
                bot_instance_id=bot_instance_id,
                idempotency_key=idempotency_key,
                broker=broker,
                event_type=event_type,
                payload=payload,
            )
            if event_type == "order_unknown":
                await self.enqueue_unknown_order(
                    bot_instance_id=bot_instance_id,
                    idempotency_key=idempotency_key,
                    signal_id=str(payload.get("signal_id") or "") or None,
                    payload=payload,
                )
        else:
            raise ValueError(f"Unknown order lifecycle event_type: {event_type}")
