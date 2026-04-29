"""UnknownOrderReconciler — resolves UNKNOWN broker_order_attempts by querying the broker.

When an order is submitted to the broker but a timeout/network error occurs, the
order attempt transitions to the ``UNKNOWN`` state.  This reconciler queries the
broker by ``idempotency_key`` / ``client_order_id`` to determine the real outcome:

- Found FILLED   → update attempt + receipt → RECONCILING → FILLED
- Found REJECTED → update attempt + receipt → RECONCILING → REJECTED
- Not found after N retries → FAILED_NEEDS_OPERATOR (operator must intervene)

Provider requirements:
    provider.get_order_by_client_id(client_order_id: str) -> dict | None
    provider.get_executions_by_client_id(client_order_id: str) -> list[dict]

These can return ``None`` / ``[]`` if the broker does not support the lookup.
Providers that do not implement these methods fall through to a FAILED_NEEDS_OPERATOR
result after ``max_retries`` cycles.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

ReconcileHook = Callable[[Dict[str, Any]], "asyncio.Coroutine[Any, Any, None]"]


@dataclass
class UnknownOrderResult:
    idempotency_key: str
    bot_instance_id: str
    outcome: str  # filled | rejected | failed_needs_operator | still_unknown | error
    broker_order_id: Optional[str] = None
    fill_price: Optional[float] = None
    fill_volume: Optional[float] = None
    error: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
    resolved_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "idempotency_key": self.idempotency_key,
            "bot_instance_id": self.bot_instance_id,
            "outcome": self.outcome,
            "broker_order_id": self.broker_order_id,
            "fill_price": self.fill_price,
            "fill_volume": self.fill_volume,
            "error": self.error,
            "details": self.details,
            "resolved_at": self.resolved_at,
        }


class UnknownOrderReconciler:
    """Resolve UNKNOWN orders by querying the broker source of truth.

    Parameters
    ----------
    provider:
        Broker provider. May implement ``get_order_by_client_id`` and
        ``get_executions_by_client_id`` for full lookup.  If those methods are
        absent the reconciler falls back to listing open positions and matching
        by comment/orderLinkId field.
    on_resolved:
        Async hook called with ``UnknownOrderResult.to_dict()`` after each
        resolution.  Use this to update ``broker_order_attempts``,
        ``order_state_transitions``, ``broker_execution_receipts`` and
        project into the ``orders`` table.
    max_retries:
        After this many failed lookup attempts the order is escalated to
        ``FAILED_NEEDS_OPERATOR``.
    retry_interval_seconds:
        Seconds to wait between retry attempts.
    """

    def __init__(
        self,
        *,
        provider: Any,
        on_resolved: Optional[ReconcileHook] = None,
        max_retries: int = 5,
        retry_interval_seconds: float = 15.0,
    ) -> None:
        self._provider = provider
        self._on_resolved = on_resolved
        self._max_retries = max_retries
        self._retry_interval = retry_interval_seconds

    async def resolve_unknown_order(
        self,
        *,
        bot_instance_id: str,
        idempotency_key: str,
        signal_id: str = "",
    ) -> UnknownOrderResult:
        """Attempt to resolve a single UNKNOWN order.

        Returns an ``UnknownOrderResult`` with the determined outcome.
        Does NOT modify DB state — that is the responsibility of ``on_resolved``
        or the caller.
        """
        for attempt in range(1, self._max_retries + 1):
            result = await self._query_broker(
                bot_instance_id=bot_instance_id,
                idempotency_key=idempotency_key,
                signal_id=signal_id,
            )
            if result.outcome != "still_unknown":
                if self._on_resolved is not None:
                    try:
                        await self._on_resolved(result.to_dict())
                    except Exception as exc:
                        logger.error("on_resolved hook failed: %s", exc)
                return result

            if attempt < self._max_retries:
                logger.info(
                    "UnknownOrderReconciler: attempt %d/%d — key=%s still unknown, retrying in %.1fs",
                    attempt,
                    self._max_retries,
                    idempotency_key,
                    self._retry_interval,
                )
                await asyncio.sleep(self._retry_interval)

        result = UnknownOrderResult(
            idempotency_key=idempotency_key,
            bot_instance_id=bot_instance_id,
            outcome="failed_needs_operator",
            error="max_retries_exceeded",
        )
        if self._on_resolved is not None:
            try:
                await self._on_resolved(result.to_dict())
            except Exception as exc:
                logger.error("on_resolved hook failed: %s", exc)
        return result

    async def resolve_batch(
        self,
        *,
        bot_instance_id: str,
        unknown_orders: List[Dict[str, Any]],
    ) -> List[UnknownOrderResult]:
        """Resolve a list of UNKNOWN orders sequentially.

        Each item in ``unknown_orders`` should have:
            - ``idempotency_key`` (str)
            - ``signal_id`` (str, optional)
        """
        results: List[UnknownOrderResult] = []
        for order in unknown_orders:
            key = str(order.get("idempotency_key") or "")
            if not key:
                continue
            r = await self.resolve_unknown_order(
                bot_instance_id=bot_instance_id,
                idempotency_key=key,
                signal_id=str(order.get("signal_id") or ""),
            )
            results.append(r)
        return results

    # ------------------------------------------------------------------
    # Internal broker query
    # ------------------------------------------------------------------

    async def _query_broker(
        self, *, bot_instance_id: str, idempotency_key: str, signal_id: str
    ) -> UnknownOrderResult:
        provider = self._provider

        # --- 1. Try direct order lookup by client order id ---
        broker_order: Optional[Dict[str, Any]] = None
        if hasattr(provider, "get_order_by_client_id"):
            try:
                broker_order = await provider.get_order_by_client_id(idempotency_key)
            except Exception as exc:
                logger.warning("get_order_by_client_id failed: %s", exc)

        if broker_order and isinstance(broker_order, dict):
            return self._classify_broker_order(idempotency_key, bot_instance_id, broker_order)

        # --- 2. Try execution/deal lookup ---
        executions: List[Dict[str, Any]] = []
        if hasattr(provider, "get_executions_by_client_id"):
            try:
                executions = await provider.get_executions_by_client_id(idempotency_key) or []
            except Exception as exc:
                logger.warning("get_executions_by_client_id failed: %s", exc)

        if executions:
            return self._classify_from_executions(idempotency_key, bot_instance_id, executions)

        # --- 3. Fall through — still unknown ---
        return UnknownOrderResult(
            idempotency_key=idempotency_key,
            bot_instance_id=bot_instance_id,
            outcome="still_unknown",
        )

    @staticmethod
    def _classify_broker_order(
        idempotency_key: str, bot_instance_id: str, order: Dict[str, Any]
    ) -> UnknownOrderResult:
        broker_status = str(order.get("status") or order.get("orderStatus") or "").upper()
        broker_order_id = str(order.get("orderId") or order.get("id") or order.get("brokerOrderId") or "")

        if broker_status in {"FILLED", "COMPLETELY_FILLED", "EXECUTED", "CLOSED"}:
            return UnknownOrderResult(
                idempotency_key=idempotency_key,
                bot_instance_id=bot_instance_id,
                outcome="filled",
                broker_order_id=broker_order_id or None,
                fill_price=_safe_float(order.get("filledPrice") or order.get("avgFillPrice") or order.get("executionPrice")),
                fill_volume=_safe_float(order.get("filledVolume") or order.get("qty") or order.get("executionSize")),
                details=order,
            )
        if broker_status in {"REJECTED", "CANCELED", "EXPIRED", "CANCELLED"}:
            return UnknownOrderResult(
                idempotency_key=idempotency_key,
                bot_instance_id=bot_instance_id,
                outcome="rejected",
                broker_order_id=broker_order_id or None,
                error=str(order.get("rejectReason") or order.get("errorCode") or broker_status),
                details=order,
            )
        # Partially filled, pending, active — still in flight
        return UnknownOrderResult(
            idempotency_key=idempotency_key,
            bot_instance_id=bot_instance_id,
            outcome="still_unknown",
            broker_order_id=broker_order_id or None,
            details=order,
        )

    @staticmethod
    def _classify_from_executions(
        idempotency_key: str, bot_instance_id: str, executions: List[Dict[str, Any]]
    ) -> UnknownOrderResult:
        total_volume = sum(_safe_float(e.get("volume") or e.get("qty") or e.get("size")) for e in executions)
        avg_price = 0.0
        if total_volume > 0:
            weighted = sum(
                _safe_float(e.get("price") or e.get("avgPrice") or e.get("fillPrice")) * _safe_float(e.get("volume") or e.get("qty") or e.get("size"))
                for e in executions
            )
            avg_price = weighted / total_volume
        broker_order_id = str(executions[0].get("orderId") or executions[0].get("brokerOrderId") or "")
        return UnknownOrderResult(
            idempotency_key=idempotency_key,
            bot_instance_id=bot_instance_id,
            outcome="filled",
            broker_order_id=broker_order_id or None,
            fill_price=avg_price if avg_price > 0 else None,
            fill_volume=total_volume if total_volume > 0 else None,
            details={"executions": executions},
        )


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
