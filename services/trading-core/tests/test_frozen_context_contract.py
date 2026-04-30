"""Unit tests for frozen_context_contract.validate_frozen_context_bindings."""
from __future__ import annotations
from types import SimpleNamespace
import pytest
from trading_core.runtime.pre_execution_gate import hash_gate_context, build_frozen_context_id
from trading_core.runtime.frozen_context_contract import validate_frozen_context_bindings


def _base_ctx(**overrides):
    gate_context = {
        "schema_version": "gate_context_v2",
        "symbol": "EURUSD",
        "side": "buy",
        "requested_volume": 0.1,
        "approved_volume": 0.1,
        "idempotency_key": "idem-abc",
        "account_id": "acc-123",
        "broker_name": "ctrader",
        "policy_version": "v1.0",
        "policy_version_id": "v1.0",
        "policy_status": "active",
        "policy_hash": "policy_hash_abc",
        "quote_id": "q-1",
        "quote_timestamp": 1000.0,
        "instrument_spec_hash": "spec_hash_1",
        "broker_snapshot_hash": "broker_snap_hash_1",
        "broker_account_snapshot_hash": "acct_snap_hash_1",
        "risk_context_hash": "risk_hash_1",
        "max_price_deviation_bps": 20.0,
        "frozen_context_id": "",
        "context_signature": "sig-1",
    }
    gate_context["frozen_context_id"] = build_frozen_context_id(gate_context)
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
        gate_context=gate_context,
        context_hash=hash_gate_context(gate_context),
        frozen_context_id=gate_context["frozen_context_id"],
        context_signature=str(gate_context.get("context_signature") or "sig-1"),
    )
    d.update(overrides)
    if "gate_context" in overrides:
        gc = dict(d.get("gate_context") or {})
        if "frozen_context_id" not in overrides:
            d["frozen_context_id"] = str(gc.get("frozen_context_id") or "")
        if "context_signature" not in overrides:
            d["context_signature"] = str(gc.get("context_signature") or "")
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
    gate_context = {
        "schema_version": "gate_context_v2",
        "symbol": "GBPUSD",
        "side": "buy",
        "requested_volume": 0.1,
        "approved_volume": 0.1,
        "idempotency_key": "idem-abc",
        "account_id": "acc-123",
        "broker_name": "ctrader",
        "policy_version": "v1.0",
        "policy_version_id": "v1.0",
        "policy_status": "active",
        "policy_hash": "policy_hash_abc",
        "quote_id": "q-1",
        "quote_timestamp": 1000.0,
        "instrument_spec_hash": "spec_hash_1",
        "broker_snapshot_hash": "broker_snap_hash_1",
        "broker_account_snapshot_hash": "acct_snap_hash_1",
        "risk_context_hash": "risk_hash_1",
        "max_price_deviation_bps": 20.0,
        "frozen_context_id": "",
        "context_signature": "sig-1",
    }
    gate_context["frozen_context_id"] = build_frozen_context_id(gate_context)
    ctx = _base_ctx(gate_context=gate_context, context_hash=hash_gate_context(gate_context))
    r = validate_frozen_context_bindings(request=_base_req(), context=ctx, provider_name="ctrader")
    assert not r.ok
    assert "symbol" in r.reason


def test_volume_mismatch():
    r = validate_frozen_context_bindings(request=_base_req(volume=0.2), context=_base_ctx(), provider_name="ctrader")
    assert not r.ok
    assert "volume" in r.reason


def test_entry_price_within_bps_band():
    # 20 bps at 1.1000 => max deviation 0.0022
    r = validate_frozen_context_bindings(request=_base_req(price=1.1010), context=_base_ctx(), provider_name="ctrader")
    assert r.ok


def test_entry_price_exceeds_bps_band():
    # 20 bps at 1.1000 => deviation > 0.0022 must fail
    r = validate_frozen_context_bindings(request=_base_req(price=1.1030), context=_base_ctx(), provider_name="ctrader")
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


def test_missing_gate_context_field_blocks_live() -> None:
    gate_context = {
        "schema_version": "gate_context_v2",
        "symbol": "EURUSD",
        "requested_volume": 0.1,
        "approved_volume": 0.1,
        "idempotency_key": "idem-abc",
        "account_id": "acc-123",
        "broker_name": "ctrader",
        "policy_version": "v1.0",
        "policy_version_id": "v1.0",
        "policy_status": "active",
        "policy_hash": "policy_hash_abc",
        "quote_id": "q-1",
        "quote_timestamp": 1000.0,
        "instrument_spec_hash": "spec_hash_1",
        "broker_snapshot_hash": "broker_snap_hash_1",
        "broker_account_snapshot_hash": "acct_snap_hash_1",
        "risk_context_hash": "risk_hash_1",
        "frozen_context_id": "",
        "context_signature": "sig-1",
    }
    gate_context["frozen_context_id"] = build_frozen_context_id(gate_context)
    ctx = _base_ctx(gate_context=gate_context, context_hash=hash_gate_context(gate_context))
    r = validate_frozen_context_bindings(request=_base_req(), context=ctx, provider_name="ctrader")
    assert r.ok is False
    assert "missing_gate_context_side" in r.reason


def test_missing_policy_hash_blocks_live() -> None:
    gate_context = {
        "schema_version": "gate_context_v2",
        "symbol": "EURUSD",
        "side": "buy",
        "requested_volume": 0.1,
        "approved_volume": 0.1,
        "idempotency_key": "idem-abc",
        "account_id": "acc-123",
        "broker_name": "ctrader",
        "policy_version": "v1.0",
        "policy_version_id": "v1.0",
        "policy_status": "active",
        "quote_id": "q-1",
        "quote_timestamp": 1000.0,
        "instrument_spec_hash": "spec_hash_1",
        "broker_snapshot_hash": "broker_snap_hash_1",
        "broker_account_snapshot_hash": "acct_snap_hash_1",
        "risk_context_hash": "risk_hash_1",
        "frozen_context_id": "",
        "context_signature": "sig-1",
    }
    gate_context["frozen_context_id"] = build_frozen_context_id(gate_context)
    ctx = _base_ctx(gate_context=gate_context, context_hash=hash_gate_context(gate_context))
    r = validate_frozen_context_bindings(request=_base_req(), context=ctx, provider_name="ctrader")
    assert r.ok is False
    assert "policy_hash" in r.reason
