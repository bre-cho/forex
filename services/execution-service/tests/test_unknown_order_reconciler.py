"""Tests for UnknownOrderReconciler."""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from execution_service.unknown_order_reconciler import UnknownOrderReconciler, UnknownOrderResult


def _make_provider(**methods):
    p = MagicMock()
    for name, val in methods.items():
        setattr(p, name, AsyncMock(return_value=val))
    return p


# ------------------------------------------------------------------
# resolve via get_order_by_client_id
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_filled_via_order_lookup():
    provider = _make_provider(
        get_order_by_client_id={"status": "FILLED", "orderId": "BRK-001", "filledPrice": 1.1050, "filledVolume": 0.01}
    )
    reconciler = UnknownOrderReconciler(provider=provider, max_retries=1, retry_interval_seconds=0)
    result = await reconciler.resolve_unknown_order(bot_instance_id="bot1", idempotency_key="KEY-1")
    assert result.outcome == "filled"
    assert result.broker_order_id == "BRK-001"
    assert result.fill_price == pytest.approx(1.1050)


@pytest.mark.asyncio
async def test_resolve_rejected_via_order_lookup():
    provider = _make_provider(
        get_order_by_client_id={"status": "REJECTED", "orderId": "BRK-002", "rejectReason": "insufficient_margin"}
    )
    reconciler = UnknownOrderReconciler(provider=provider, max_retries=1, retry_interval_seconds=0)
    result = await reconciler.resolve_unknown_order(bot_instance_id="bot1", idempotency_key="KEY-2")
    assert result.outcome == "rejected"
    assert result.error == "insufficient_margin"


@pytest.mark.asyncio
async def test_resolve_expired_via_order_lookup():
    provider = _make_provider(
        get_order_by_client_id={"status": "EXPIRED", "orderId": "BRK-003"}
    )
    reconciler = UnknownOrderReconciler(provider=provider, max_retries=1, retry_interval_seconds=0)
    result = await reconciler.resolve_unknown_order(bot_instance_id="bot1", idempotency_key="KEY-3")
    assert result.outcome == "rejected"


# ------------------------------------------------------------------
# resolve via get_executions_by_client_id
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_filled_via_executions():
    provider = _make_provider(
        get_order_by_client_id=None,  # not found
        get_executions_by_client_id=[
            {"orderId": "BRK-010", "price": 1.2000, "volume": 0.01},
            {"orderId": "BRK-010", "price": 1.2010, "volume": 0.01},
        ]
    )
    reconciler = UnknownOrderReconciler(provider=provider, max_retries=1, retry_interval_seconds=0)
    result = await reconciler.resolve_unknown_order(bot_instance_id="bot2", idempotency_key="KEY-10")
    assert result.outcome == "filled"
    assert result.fill_volume == pytest.approx(0.02)
    assert result.fill_price == pytest.approx(1.2005)


# ------------------------------------------------------------------
# max_retries → failed_needs_operator
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_failed_after_max_retries():
    provider = _make_provider(
        get_order_by_client_id=None,
        get_executions_by_client_id=[],
    )
    reconciler = UnknownOrderReconciler(provider=provider, max_retries=2, retry_interval_seconds=0)
    result = await reconciler.resolve_unknown_order(bot_instance_id="bot3", idempotency_key="KEY-99")
    assert result.outcome == "failed_needs_operator"
    assert result.error == "max_retries_exceeded"


# ------------------------------------------------------------------
# on_resolved hook called
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_on_resolved_hook_called():
    provider = _make_provider(
        get_order_by_client_id={"status": "FILLED", "orderId": "BRK-020", "filledPrice": 1.0, "filledVolume": 0.1}
    )
    hook_calls = []
    async def hook(payload):
        hook_calls.append(payload)
    reconciler = UnknownOrderReconciler(provider=provider, on_resolved=hook, max_retries=1, retry_interval_seconds=0)
    await reconciler.resolve_unknown_order(bot_instance_id="bot4", idempotency_key="KEY-20")
    assert len(hook_calls) == 1
    assert hook_calls[0]["outcome"] == "filled"


# ------------------------------------------------------------------
# batch resolve
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_batch():
    provider = _make_provider(
        get_order_by_client_id={"status": "FILLED", "orderId": "BRK-030", "filledPrice": 1.0, "filledVolume": 0.01}
    )
    reconciler = UnknownOrderReconciler(provider=provider, max_retries=1, retry_interval_seconds=0)
    results = await reconciler.resolve_batch(
        bot_instance_id="bot5",
        unknown_orders=[{"idempotency_key": "K1"}, {"idempotency_key": "K2"}],
    )
    assert len(results) == 2
    assert all(r.outcome == "filled" for r in results)


# ------------------------------------------------------------------
# provider without lookup methods → failed_needs_operator
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_provider_without_lookup_methods():
    provider = MagicMock(spec=[])  # no get_order_by_client_id / get_executions_by_client_id
    reconciler = UnknownOrderReconciler(provider=provider, max_retries=2, retry_interval_seconds=0)
    result = await reconciler.resolve_unknown_order(bot_instance_id="bot6", idempotency_key="KEY-no-lookup")
    assert result.outcome == "failed_needs_operator"
