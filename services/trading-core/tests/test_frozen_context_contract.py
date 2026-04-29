"""Unit tests for frozen_context_contract.validate_frozen_context_bindings."""
from __future__ import annotations
from types import SimpleNamespace
import pytest
from trading_core.runtime.frozen_context_contract import validate_frozen_context_bindings


def _base_ctx(**overrides):
    d = dict(
        bot_instance_id="bot-1",
        idempotency_key="idem-abc",
        brain_cycle_id="cycle-1",
        account_id="acc-123",
        broker_name="ctrader",
        policy_version="v1.0",
        order_type="market",
        entry_price=1.1000,
        stop_loss=1.0900,
        take_profit=1.1200,
        gate_context={"symbol": "EURUSD", "side": "buy", "requested_volume": 0.1},
    )
    d.update(overrides)
    return SimpleNamespace(**d)


def _base_req(**overrides):
    d = dict(symbol="EURUSD", side="buy", volume=0.1, order_type="market", price=1.1000, stop_loss=1.0900, take_profit=1.1200)
    d.update(overrides)
    return SimpleNamespace(**d)


def test_valid_binding():
    r = validate_frozen_context_bindings(request=_base_req(), context=_base_ctx(), provider_name="ctrader")
    assert r.ok is True


def test_missing_bot_instance_id():
    r = validate_frozen_context_bindings(request=_base_req(), context=_base_ctx(bot_instance_id=""), provider_name="ctrader")
    assert not r.ok
    assert "bot_instance_id" in r.reason


def test_broker_name_mismatch():
    r = validate_frozen_context_bindings(request=_base_req(), context=_base_ctx(), provider_name="oanda")
    assert not r.ok
    assert "broker_name" in r.reason


def test_broker_name_case_insensitive():
    r = validate_frozen_context_bindings(request=_base_req(), context=_base_ctx(broker_name="CTRADER"), provider_name="ctrader")
    assert r.ok


def test_symbol_mismatch():
    ctx = _base_ctx(gate_context={"symbol": "GBPUSD", "side": "buy", "requested_volume": 0.1})
    r = validate_frozen_context_bindings(request=_base_req(), context=ctx, provider_name="ctrader")
    assert not r.ok
    assert "symbol" in r.reason


def test_volume_mismatch():
    r = validate_frozen_context_bindings(request=_base_req(volume=0.2), context=_base_ctx(), provider_name="ctrader")
    assert not r.ok
    assert "volume" in r.reason


def test_entry_price_within_5pct_band():
    # 1.1000 * 0.04 = 0.044 deviation — within 5%
    r = validate_frozen_context_bindings(request=_base_req(price=1.1044), context=_base_ctx(), provider_name="ctrader")
    assert r.ok


def test_entry_price_exceeds_5pct_band():
    # 1.1000 * 0.06 = 0.066 deviation — outside 5%
    r = validate_frozen_context_bindings(request=_base_req(price=1.1066 + 1.1000 * 0.06), context=_base_ctx(), provider_name="ctrader")
    assert not r.ok
    assert "entry_price" in r.reason


def test_stop_loss_mismatch():
    r = validate_frozen_context_bindings(request=_base_req(stop_loss=1.0800), context=_base_ctx(), provider_name="ctrader")
    assert not r.ok
    assert "stop_loss" in r.reason


def test_take_profit_mismatch():
    r = validate_frozen_context_bindings(request=_base_req(take_profit=1.1300), context=_base_ctx(), provider_name="ctrader")
    assert not r.ok
    assert "take_profit" in r.reason


def test_missing_policy_version():
    r = validate_frozen_context_bindings(request=_base_req(), context=_base_ctx(policy_version=""), provider_name="ctrader")
    assert not r.ok
    assert "policy_version" in r.reason
