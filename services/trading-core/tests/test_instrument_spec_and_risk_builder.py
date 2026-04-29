"""Tests for P0-C: InstrumentSpec and P0-B: RiskContextBuilder with live mode."""
from __future__ import annotations

import pytest
from trading_core.risk.instrument_spec import InstrumentSpec, get_fallback_spec
from trading_core.risk.risk_context_builder import RiskContextBuilder


class MockAccountInfo:
    def __init__(self, equity=1000.0, free_margin=800.0, margin=200.0):
        self.equity = equity
        self.free_margin = free_margin
        self.margin = margin


# ── InstrumentSpec basic calculations ─────────────────────────────────────────

def test_instrument_spec_is_complete_full():
    spec = InstrumentSpec("EURUSD", "forex", 100000, 0.0001, 0.00001, 0.01, 500.0, 0.01, 0.01, "USD", "EUR")
    assert spec.is_complete()


def test_instrument_spec_is_complete_missing_margin_rate():
    spec = InstrumentSpec("EURUSD", "forex", 100000, 0.0001, 0.00001, 0.01, 500.0, 0.01, 0.0, "USD", "EUR")
    assert not spec.is_complete()


def test_instrument_spec_estimated_margin():
    spec = InstrumentSpec("EURUSD", "forex", 100000, 0.0001, 0.00001, 0.01, 500.0, 0.01, 0.02, "USD", "EUR")
    margin = spec.estimated_margin(volume=0.1, price=1.10)
    # 0.1 * 100000 * 1.10 * 0.02 = 220
    assert margin == pytest.approx(220.0)


def test_instrument_spec_pip_value():
    spec = InstrumentSpec("EURUSD", "forex", 100000, 0.0001, 0.00001, 0.01, 500.0, 0.01, 0.01, "USD", "EUR")
    pip_val = spec.pip_value_per_lot()
    assert pip_val == pytest.approx(10.0)  # 0.0001 * 100000


def test_get_fallback_spec_known():
    spec = get_fallback_spec("EURUSD")
    assert spec is not None
    assert spec.contract_size == 100000


def test_get_fallback_spec_unknown():
    assert get_fallback_spec("EXOTIC_XYZ") is None


# ── RiskContextBuilder paper/backtest with fallback spec ──────────────────────

def test_risk_context_builder_paper_uses_fallback():
    info = MockAccountInfo(equity=1000.0, free_margin=800.0, margin=200.0)
    ctx = RiskContextBuilder.build(
        account_info=info,
        open_positions=[],
        symbol="EURUSD",
        entry_price=1.10,
        stop_loss=1.09,
        requested_volume=0.1,
        risk_pct=1.0,
        runtime_mode="paper",
    )
    # With real contract_size=100000, margin_rate=0.01
    # notional = 0.1 * 100000 * 1.10 = 11000
    # margin cost = 11000 * 0.01 = 110
    # free_margin_after = 800 - 110 = 690
    assert ctx.free_margin_after_order == pytest.approx(690.0, abs=1.0)
    assert ctx.margin_usage_pct > 0


def test_risk_context_builder_live_requires_spec():
    info = MockAccountInfo()
    with pytest.raises(RuntimeError, match="risk_context_missing_instrument_spec"):
        RiskContextBuilder.build(
            account_info=info,
            open_positions=[],
            symbol="EXOTIC_XYZ",
            entry_price=1.0,
            stop_loss=0.99,
            requested_volume=0.1,
            risk_pct=1.0,
            instrument_spec=None,
            runtime_mode="live",
        )


def test_risk_context_builder_live_with_spec():
    spec = InstrumentSpec("BTCUSDT", "crypto", 1, 1.0, 0.1, 0.001, 100.0, 0.001, 0.1, "USDT", "BTC")
    info = MockAccountInfo(equity=10000.0, free_margin=8000.0, margin=2000.0)
    ctx = RiskContextBuilder.build(
        account_info=info,
        open_positions=[],
        symbol="BTCUSDT",
        entry_price=30000.0,
        stop_loss=29000.0,
        requested_volume=0.01,
        risk_pct=1.0,
        instrument_spec=spec,
        runtime_mode="live",
    )
    # notional = 0.01 * 1 * 30000 = 300
    # margin = 2000 + 300 * 0.1 = 2030
    # free_margin_after = 8000 - 30 = 7970
    assert ctx.free_margin_after_order == pytest.approx(7970.0, abs=1.0)
    assert ctx.max_loss_amount_if_sl_hit > 0


# ── RiskContextBuilder raises if equity is 0 ─────────────────────────────────

def test_risk_context_builder_raises_if_no_equity():
    info = MockAccountInfo(equity=0.0)
    with pytest.raises(RuntimeError, match="risk_context_missing_equity"):
        RiskContextBuilder.build(
            account_info=info,
            open_positions=[],
            symbol="EURUSD",
            entry_price=1.10,
            stop_loss=None,
            requested_volume=0.1,
            risk_pct=1.0,
            runtime_mode="paper",
        )
