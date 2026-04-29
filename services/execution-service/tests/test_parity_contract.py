from __future__ import annotations

from execution_service.parity_contract import validate_order_contract


def _base() -> dict:
    return {
        "signal_id": "sig-1",
        "symbol": "EURUSD",
        "side": "BUY",
        "volume": 0.01,
        "order_type": "market",
    }


def test_backtest_contract_core_only() -> None:
    res = validate_order_contract("backtest", _base())
    assert res.ok is True


def test_paper_contract_core_only() -> None:
    res = validate_order_contract("paper", _base())
    assert res.ok is True


def test_demo_requires_governance_fields() -> None:
    res = validate_order_contract("demo", _base())
    assert res.ok is False
    assert "idempotency_key" in res.missing

    env = {
        **_base(),
        "idempotency_key": "idem-1",
        "brain_cycle_id": "cycle-1",
        "pre_execution_context": {"provider_mode": "demo"},
    }
    ok = validate_order_contract("demo", env)
    assert ok.ok is True


def test_live_requires_receipt_on_success() -> None:
    env = {
        **_base(),
        "idempotency_key": "idem-1",
        "brain_cycle_id": "cycle-1",
        "pre_execution_context": {"provider_mode": "live"},
        "success": True,
        "submit_status": "ACKED",
        "fill_status": "FILLED",
        "broker_order_id": "bo-1",
    }
    assert validate_order_contract("live", env).ok is True

    bad = dict(env)
    bad.pop("broker_order_id")
    failed = validate_order_contract("live", bad)
    assert failed.ok is False
    assert failed.reason == "missing_live_receipt_fields"
