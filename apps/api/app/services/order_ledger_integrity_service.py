from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BrokerExecutionReceipt,
    BrokerOrderAttempt,
    Order,
    OrderStateTransition,
    ReconciliationQueueItem,
    SubmitOutbox,
)


@dataclass(frozen=True)
class IntegrityIssue:
    severity: str
    code: str
    bot_instance_id: str
    idempotency_key: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "bot_instance_id": self.bot_instance_id,
            "idempotency_key": self.idempotency_key,
            "detail": self.detail,
        }


class OrderLedgerIntegrityService:
    """Nightly invariants for order ledger, submit outbox, and reconcile queue."""

    _UNRESOLVED_QUEUE_STATUSES = {"pending", "retry", "in_progress", "failed_needs_operator"}

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def run(
        self,
        *,
        bot_instance_id: str | None = None,
        stale_submit_seconds: float = 120.0,
    ) -> dict[str, Any]:
        issues: list[IntegrityIssue] = []

        orders_stmt = select(Order)
        attempts_stmt = select(BrokerOrderAttempt)
        receipts_stmt = select(BrokerExecutionReceipt)
        queue_stmt = select(ReconciliationQueueItem)
        outbox_stmt = select(SubmitOutbox)

        if bot_instance_id:
            orders_stmt = orders_stmt.where(Order.bot_instance_id == bot_instance_id)
            attempts_stmt = attempts_stmt.where(BrokerOrderAttempt.bot_instance_id == bot_instance_id)
            receipts_stmt = receipts_stmt.where(BrokerExecutionReceipt.bot_instance_id == bot_instance_id)
            queue_stmt = queue_stmt.where(ReconciliationQueueItem.bot_instance_id == bot_instance_id)
            outbox_stmt = outbox_stmt.where(SubmitOutbox.bot_instance_id == bot_instance_id)

        orders = (await self.db.execute(orders_stmt)).scalars().all()
        attempts = (await self.db.execute(attempts_stmt)).scalars().all()
        receipts = (await self.db.execute(receipts_stmt)).scalars().all()
        queue_items = (await self.db.execute(queue_stmt)).scalars().all()
        outbox_rows = (await self.db.execute(outbox_stmt)).scalars().all()

        attempt_map: dict[tuple[str, str], BrokerOrderAttempt] = {
            (str(a.bot_instance_id), str(a.idempotency_key)): a for a in attempts
        }

        receipt_keys: set[tuple[str, str]] = {
            (str(r.bot_instance_id), str(r.idempotency_key)) for r in receipts
        }

        unresolved_queue_keys: set[tuple[str, str]] = {
            (str(q.bot_instance_id), str(q.idempotency_key))
            for q in queue_items
            if str(q.status or "").lower() in self._UNRESOLVED_QUEUE_STATUSES
        }

        # 1) orders.current_state must match latest transition.to_state
        for order in orders:
            b = str(order.bot_instance_id)
            idem = str(order.idempotency_key or "")
            if not idem:
                continue
            latest_transition = (
                (
                    await self.db.execute(
                        select(OrderStateTransition)
                        .where(
                            OrderStateTransition.bot_instance_id == b,
                            OrderStateTransition.idempotency_key == idem,
                        )
                        .order_by(OrderStateTransition.created_at.desc(), OrderStateTransition.id.desc())
                        .limit(1)
                    )
                )
                .scalar_one_or_none()
            )
            if latest_transition is not None:
                latest_to_state = str(latest_transition.to_state or "")
                order_state = str(order.current_state or "")
                if order_state and latest_to_state and order_state.upper() != latest_to_state.upper():
                    issues.append(
                        IntegrityIssue(
                            severity="critical",
                            code="order_state_projection_mismatch",
                            bot_instance_id=b,
                            idempotency_key=idem,
                            detail=f"orders.current_state={order_state} latest_transition.to_state={latest_to_state}",
                        )
                    )

        # 2) filled / partial orders must have broker receipt
        for order in orders:
            b = str(order.bot_instance_id)
            idem = str(order.idempotency_key or "")
            if not idem:
                continue
            status = str(order.status or "").lower()
            if status in {"filled", "partially_filled"} and (b, idem) not in receipt_keys:
                issues.append(
                    IntegrityIssue(
                        severity="critical",
                        code="filled_without_receipt",
                        bot_instance_id=b,
                        idempotency_key=idem,
                        detail=f"order.status={status} but no broker_execution_receipts row",
                    )
                )

        # 3) unknown/reconciling orders must have unresolved queue item
        for order in orders:
            b = str(order.bot_instance_id)
            idem = str(order.idempotency_key or "")
            if not idem:
                continue
            status = str(order.status or "").lower()
            current_state = str(order.current_state or "").upper()
            if status in {"unknown", "reconciling"} or current_state in {"UNKNOWN", "RECONCILING"}:
                if (b, idem) not in unresolved_queue_keys:
                    issues.append(
                        IntegrityIssue(
                            severity="critical",
                            code="unknown_without_reconciliation_queue",
                            bot_instance_id=b,
                            idempotency_key=idem,
                            detail=f"order.status={status} current_state={current_state} has no unresolved queue item",
                        )
                    )

        # 4) rejected orders must carry reason from attempt/transition
        for order in orders:
            b = str(order.bot_instance_id)
            idem = str(order.idempotency_key or "")
            if not idem:
                continue
            status = str(order.status or "").lower()
            if status != "rejected":
                continue
            attempt = attempt_map.get((b, idem))
            has_reason = bool(str(getattr(attempt, "error_message", "") or "").strip())
            if not has_reason:
                latest_reject_transition = (
                    (
                        await self.db.execute(
                            select(OrderStateTransition)
                            .where(
                                OrderStateTransition.bot_instance_id == b,
                                OrderStateTransition.idempotency_key == idem,
                                OrderStateTransition.to_state == "REJECTED",
                            )
                            .order_by(OrderStateTransition.created_at.desc(), OrderStateTransition.id.desc())
                            .limit(1)
                        )
                    )
                    .scalar_one_or_none()
                )
                has_reason = bool(str(getattr(latest_reject_transition, "detail", "") or "").strip())
            if not has_reason:
                issues.append(
                    IntegrityIssue(
                        severity="warning",
                        code="rejected_without_reason",
                        bot_instance_id=b,
                        idempotency_key=idem,
                        detail="rejected order has no attempt.error_message and no transition detail",
                    )
                )

        # 5) submit_outbox invariants
        now = datetime.now(timezone.utc)
        for row in outbox_rows:
            b = str(row.bot_instance_id)
            idem = str(row.idempotency_key)
            phase = str(row.phase or "")
            key = (b, idem)

            updated_at = row.updated_at or row.created_at
            if updated_at is not None and updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)

            if phase == "UNKNOWN_AFTER_SEND" and key not in unresolved_queue_keys:
                issues.append(
                    IntegrityIssue(
                        severity="critical",
                        code="submit_outbox_unknown_after_send_without_queue",
                        bot_instance_id=b,
                        idempotency_key=idem,
                        detail="submit_outbox phase UNKNOWN_AFTER_SEND but reconciliation queue unresolved item missing",
                    )
                )

            if phase == "BROKER_SEND_STARTED" and updated_at is not None:
                age = (now - updated_at).total_seconds()
                if age > float(stale_submit_seconds) and key not in unresolved_queue_keys and key not in receipt_keys:
                    issues.append(
                        IntegrityIssue(
                            severity="warning",
                            code="submit_outbox_stale_send_started",
                            bot_instance_id=b,
                            idempotency_key=idem,
                            detail=f"BROKER_SEND_STARTED stale for {age:.1f}s with no receipt and no unresolved queue",
                        )
                    )

            if phase == "BROKER_SEND_RETURNED" and key not in receipt_keys:
                issues.append(
                    IntegrityIssue(
                        severity="warning",
                        code="submit_outbox_returned_without_receipt",
                        bot_instance_id=b,
                        idempotency_key=idem,
                        detail="BROKER_SEND_RETURNED present but broker_execution_receipt missing",
                    )
                )

        # 6) unresolved queue should align with attempt states
        for item in queue_items:
            status = str(item.status or "").lower()
            if status not in self._UNRESOLVED_QUEUE_STATUSES:
                continue
            b = str(item.bot_instance_id)
            idem = str(item.idempotency_key)
            attempt = attempt_map.get((b, idem))
            if attempt is None:
                issues.append(
                    IntegrityIssue(
                        severity="critical",
                        code="queue_item_without_attempt",
                        bot_instance_id=b,
                        idempotency_key=idem,
                        detail=f"unresolved queue item status={status} has no broker_order_attempt",
                    )
                )
                continue
            current_state = str(getattr(attempt, "current_state", "") or "").upper()
            if current_state not in {"UNKNOWN", "RECONCILING", "FAILED_NEEDS_OPERATOR", "SUBMITTED"}:
                issues.append(
                    IntegrityIssue(
                        severity="warning",
                        code="queue_item_attempt_state_unexpected",
                        bot_instance_id=b,
                        idempotency_key=idem,
                        detail=f"queue status={status} but attempt.current_state={current_state}",
                    )
                )

        critical = sum(1 for i in issues if i.severity == "critical")
        warning = sum(1 for i in issues if i.severity == "warning")

        return {
            "ok": critical == 0,
            "critical_count": critical,
            "warning_count": warning,
            "issue_count": len(issues),
            "issues": [i.to_dict() for i in issues],
        }
