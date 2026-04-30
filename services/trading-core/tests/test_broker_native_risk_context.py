"""Unit tests for broker_native_risk_context.estimate_live_margin_required."""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock
from trading_core.risk.broker_native_risk_context import CurrencyConversionService, estimate_live_margin_required


@pytest.mark.asyncio
async def test_returns_margin_from_provider():
    provider = MagicMock()
    provider.estimate_margin = AsyncMock(return_value=50.0)
    result = await estimate_live_margin_required(provider=provider, symbol="EURUSD", side="buy", volume=0.1, price=1.1)
    assert result == 50.0


@pytest.mark.asyncio
async def test_raises_if_estimate_margin_missing():
    provider = MagicMock(spec=[])
    with pytest.raises(RuntimeError, match="broker_margin_estimate_unavailable"):
        await estimate_live_margin_required(provider=provider, symbol="EURUSD", side="buy", volume=0.1, price=1.1)


@pytest.mark.asyncio
async def test_raises_if_estimate_returns_zero():
    provider = MagicMock()
    provider.estimate_margin = AsyncMock(return_value=0.0)
    with pytest.raises(RuntimeError, match="broker_margin_estimate_invalid"):
        await estimate_live_margin_required(provider=provider, symbol="EURUSD", side="buy", volume=0.1, price=1.1)


@pytest.mark.asyncio
async def test_raises_if_provider_throws():
    provider = MagicMock()
    provider.estimate_margin = AsyncMock(side_effect=Exception("timeout"))
    with pytest.raises(RuntimeError, match="broker_margin_estimate_failed"):
        await estimate_live_margin_required(provider=provider, symbol="EURUSD", side="buy", volume=0.1, price=1.1)


def test_currency_conversion_service_direct_rate_from_quote():
    svc = CurrencyConversionService(
        quote_snapshots={
            "EURUSD": {"bid": 1.10, "ask": 1.12},
        },
        runtime_mode="live",
    )
    amount_usd = svc.convert_amount(amount=100.0, from_currency="EUR", to_currency="USD")
    assert amount_usd == pytest.approx(111.0)


def test_currency_conversion_service_inverse_rate_from_quote():
    svc = CurrencyConversionService(
        quote_snapshots={
            "USDJPY": {"bid": 149.0, "ask": 151.0},
        },
        runtime_mode="live",
    )
    amount_usd = svc.convert_amount(amount=15000.0, from_currency="JPY", to_currency="USD")
    assert amount_usd == pytest.approx(100.0)
