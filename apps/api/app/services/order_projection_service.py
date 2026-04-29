"""OrderProjectionService — keeps `orders` table as a read-model projection from the execution ledger.

The execution ledger (`broker_order_attempts`, `order_state_transitions`,
`broker_execution_receipts`) is the *source of truth* for order status.
This service upserts the `orders` table from ledger events so that operator
UIs reading the `orders` table see the real state without touching the ledger
directly.

Rule: never create an `Order` row from a raw event payload that lacks an
`idempotency_key`. If the attempt is unknown, insert with status ``unknown``
and let the reconciler resolve it.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BrokerOrderAttempt, BrokerExecutionReceipt, Order

logger = logging.getLogger(__name__)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class OrderProjectionService:
    """Upserts `orders` rows from ledger events."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def upsert_from_order_attempt(
        self,
        bot_instance_id: str,
        attempt: BrokerOrderAttempt,
    ) -> Order:
        """Create or update an Order row based on an order attempt."""
        idempotency_key = str(attempt.idempotency_key or "")
        if not idempotency_key:
            logger.warning(
                "upsert_from_order_attempt called with no idempotency_key for bot %s",
                bot_instance_id,
            )

        existing = await self._find_by_idempotency(bot_instance_id, idempotency_key)
        if existing is not None:
            existing.status = _map_attempt_state_to_order_status(attempt.current_state)
            existing.current_state = str(attempt.current_state or "") or None
            existing.last_transition_at = _now_utc()
            existing.reconciliation_status = "reconciling" if str(attempt.current_state or "").upper() in {"UNKNOWN", "RECONCILING"} else "ok"
            existing.updated_at = _now_utc()
            await self.db.commit()
            await self.db.refresh(existing)
            return existing

        row = Order(
            bot_instance_id=bot_instance_id,
            broker_order_id=str(attempt.broker_order_id) if attempt.broker_order_id else None,
            idempotency_key=idempotency_key or None,
            source_attempt_id=attempt.id if hasattr(attempt, "id") else None,
            symbol=str(attempt.symbol or ""),
            side=str(attempt.side or ""),
            order_type=str((attempt.request_payload or {}).get("order_type") or "market"),
            volume=float(attempt.volume or 0.0),
            price=float((attempt.request_payload or {}).get("price")) if (attempt.request_payload or {}).get("price") else None,
            status=_map_attempt_state_to_order_status(attempt.current_state),
            current_state=str(attempt.current_state or "") or None,
            reconciliation_status="reconciling" if str(attempt.current_state or "").upper() in {"UNKNOWN", "RECONCILING"} else "ok",
            last_transition_at=_now_utc(),
            created_at=_now_utc(),
            updated_at=_now_utc(),
        )
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def upsert_from_execution_receipt(
        self,
        bot_instance_id: str,
        receipt: BrokerExecutionReceipt,
    ) -> Order | None:
        """Update order status from an execution receipt."""
        idempotency_key = str(receipt.idempotency_key or "")
        existing = await self._find_by_idempotency(bot_instance_id, idempotency_key)
        if existing is None:
            logger.warning(
                "No Order row found for idempotency_key=%s bot=%s — cannot project receipt",
                idempotency_key,
                bot_instance_id,
            )
            return None

        fill_status = str(receipt.fill_status or "")
        if fill_status == "FILLED":
            existing.status = "filled"
        elif fill_status == "PARTIAL":
            existing.status = "partially_filled"
        elif fill_status == "REJECTED":
            existing.status = "rejected"
        elif fill_status == "UNKNOWN":
            existing.status = "unknown"
        # else keep current status

        if receipt.broker_order_id:
            existing.broker_order_id = str(receipt.broker_order_id)
        existing.submit_status = str(receipt.submit_status or "") or None
        existing.fill_status = str(receipt.fill_status or "") or None
        existing.broker_position_id = str(receipt.broker_position_id or "") or None
        existing.broker_deal_id = str(receipt.broker_deal_id or "") or None
        existing.avg_fill_price = float(receipt.avg_fill_price) if receipt.avg_fill_price is not None else existing.avg_fill_price
        existing.filled_volume = float(receipt.filled_volume or 0.0)
        existing.reconciliation_status = "reconciling" if str(receipt.fill_status or "").upper() == "UNKNOWN" else "ok"
        existing.last_transition_at = _now_utc()
        existing.updated_at = _now_utc()
        await self.db.commit()
        await self.db.refresh(existing)
        return existing

    async def sync_order_status_from_state_transition(
        self,
        bot_instance_id: str,
        idempotency_key: str,
        new_state: str,
    ) -> None:
        """Update order status whenever a state transition is recorded."""
        existing = await self._find_by_idempotency(bot_instance_id, idempotency_key)
        if existing is None:
            return
        existing.status = _map_attempt_state_to_order_status(new_state)
        existing.current_state = str(new_state or "") or None
        existing.reconciliation_status = "reconciling" if str(new_state or "").upper() in {"UNKNOWN", "RECONCILING"} else "ok"
        existing.last_transition_at = _now_utc()
        existing.updated_at = _now_utc()
        await self.db.commit()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _find_by_idempotency(
        self, bot_instance_id: str, idempotency_key: str
    ) -> Order | None:
        if not idempotency_key:
            return None
        stmt = select(Order).where(
            Order.bot_instance_id == bot_instance_id,
            Order.idempotency_key == idempotency_key,
        ).limit(1)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()


# ------------------------------------------------------------------
# State mapping helpers
# ------------------------------------------------------------------

_ATTEMPT_STATE_TO_ORDER_STATUS: dict[str, str] = {
    "INTENT_CREATED": "pending",
    "GATE_ALLOWED": "pending",
    "GATE_BLOCKED": "rejected",
    "RESERVED": "pending",
    "SUBMITTED": "submitted",
    "ACKED": "acked",
    "FILLED": "filled",
    "PARTIAL": "partially_filled",
    "REJECTED": "rejected",
    "UNKNOWN": "unknown",
    "RECONCILING": "reconciling",
    "OPEN_POSITION_VERIFIED": "filled",
    "CLOSED": "closed",
    "FAILED_NEEDS_OPERATOR": "failed",
}


def _map_attempt_state_to_order_status(state: str | None) -> str:
    if not state:
        return "unknown"
    return _ATTEMPT_STATE_TO_ORDER_STATUS.get(str(state).upper(), "unknown")
