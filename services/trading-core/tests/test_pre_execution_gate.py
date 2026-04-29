"""Tests for PreExecutionGate — P0 acceptance criteria."""
from __future__ import annotations

import pytest

from trading_core.runtime.pre_execution_gate import GateResult, PreExecutionGate

DEFAULT_POLICY: dict = {
    "max_daily_loss_pct": 5.0,
    "daily_take_profit_amount": 500.0,
    "max_consecutive_losses": 4,
    "max_spread_pips": 2.0,
    "max_open_positions": 3,
    "min_confidence": 0.65,
    "min_rr": 1.5,
    "max_data_age_seconds": 30,
}


def _ctx(**kwargs) -> dict:
    base = {
        "provider_mode": "live",
        "runtime_mode": "live",
        "broker_connected": True,
        "market_data_ok": True,
        "data_age_seconds": 0.0,
        "daily_profit_amount": 0.0,
        "daily_loss_pct": 0.0,
        "consecutive_losses": 0,
        "spread_pips": 0.5,
        "confidence": 0.80,
        "rr": 2.0,
        "open_positions": 0,
        "idempotency_exists": False,
        "kill_switch": False,
    }
    base.update(kwargs)
    return base


@pytest.fixture()
def gate():
    return PreExecutionGate(policy=DEFAULT_POLICY)


def test_allow_clean_context(gate):
    result = gate.evaluate(_ctx())
    assert result.action == "ALLOW"


def test_kill_switch_blocks(gate):
    result = gate.evaluate(_ctx(kill_switch=True))
    assert result.action == "BLOCK"
    assert result.reason == "kill_switch_enabled"


def test_live_stub_provider_blocked(gate):
    """live runtime + stub provider must be blocked."""
    result = gate.evaluate(_ctx(provider_mode="stub"))
    assert result.action == "BLOCK"
    assert result.reason == "provider_not_live_capable"


def test_live_degraded_provider_blocked(gate):
    result = gate.evaluate(_ctx(provider_mode="degraded"))
    assert result.action == "BLOCK"


def test_broker_not_connected_blocked(gate):
    result = gate.evaluate(_ctx(broker_connected=False))
    assert result.action == "BLOCK"
    assert result.reason == "broker_not_connected"


def test_stale_data_blocked(gate):
    result = gate.evaluate(_ctx(data_age_seconds=60))
    assert result.action == "BLOCK"
    assert result.reason == "market_data_stale"


def test_invalid_market_data_blocked(gate):
    result = gate.evaluate(_ctx(market_data_ok=False))
    assert result.action == "BLOCK"
    assert result.reason == "market_data_invalid"


def test_daily_loss_limit_blocked(gate):
    result = gate.evaluate(_ctx(daily_loss_pct=5.0))
    assert result.action == "BLOCK"
    assert result.reason == "daily_loss_limit_hit"


def test_daily_take_profit_blocked(gate):
    result = gate.evaluate(_ctx(daily_profit_amount=500.0))
    assert result.action == "BLOCK"
    assert result.reason == "daily_take_profit_hit"


def test_daily_take_profit_percent_equity_blocked():
    gate = PreExecutionGate(
        policy={
            **DEFAULT_POLICY,
            "daily_take_profit_mode": "percent_equity",
            "daily_take_profit_pct": 2.0,
        }
    )
    result = gate.evaluate(_ctx(starting_equity=10000.0, daily_profit_amount=200.0))
    assert result.action == "BLOCK"
    assert result.reason == "daily_take_profit_hit"


def test_consecutive_losses_blocked(gate):
    result = gate.evaluate(_ctx(consecutive_losses=4))
    assert result.action == "BLOCK"
    assert result.reason == "consecutive_loss_limit_hit"


def test_spread_too_high_blocked(gate):
    result = gate.evaluate(_ctx(spread_pips=3.0))
    assert result.action == "BLOCK"
    assert result.reason == "spread_too_high"


def test_max_open_positions_blocked(gate):
    result = gate.evaluate(_ctx(open_positions=3))
    assert result.action == "BLOCK"
    assert result.reason == "max_open_positions_hit"


def test_duplicate_order_blocked(gate):
    result = gate.evaluate(_ctx(idempotency_exists=True))
    assert result.action == "BLOCK"
    assert result.reason == "duplicate_order_blocked"


def test_low_confidence_skipped(gate):
    result = gate.evaluate(_ctx(confidence=0.50))
    assert result.action == "SKIP"
    assert result.reason == "confidence_too_low"


def test_low_rr_skipped(gate):
    result = gate.evaluate(_ctx(rr=1.0))
    assert result.action == "SKIP"
    assert result.reason == "rr_too_low"


def test_paper_stub_provider_allowed():
    """paper mode with stub provider is fine."""
    gate = PreExecutionGate(policy=DEFAULT_POLICY)
    result = gate.evaluate(_ctx(provider_mode="stub", runtime_mode="paper"))
    assert result.action == "ALLOW"
