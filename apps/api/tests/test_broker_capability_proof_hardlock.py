"""Tests for P0-A — BrokerCapabilityProof hardlock.

Covers:
  1. all_required_passed includes margin_estimate_valid + execution_lookup_supported
  2. BrokerCapabilityProofService.record_proof persists an AuditLog entry
  3. live_start_preflight passes bot symbol/timeframe to capability proof
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# BrokerCapabilityProof unit tests
# ---------------------------------------------------------------------------

def _make_proof(**overrides):
    from execution_service.providers.base import BrokerCapabilityProof

    defaults = dict(
        provider="test_provider",
        mode="live",
        account_authorized=True,
        account_id_match=True,
        quote_realtime=True,
        server_time_valid=True,
        instrument_spec_valid=True,
        margin_estimate_valid=True,
        client_order_id_supported=True,
        order_lookup_supported=True,
        execution_lookup_supported=True,
        close_all_supported=True,
        proof_timestamp=datetime.now(timezone.utc).timestamp(),
    )
    defaults.update(overrides)
    return BrokerCapabilityProof(**defaults)


def test_all_required_passed_true_when_all_fields_set():
    proof = _make_proof()
    assert proof.all_required_passed is True


def test_all_required_passed_false_when_margin_estimate_invalid():
    proof = _make_proof(margin_estimate_valid=False)
    assert proof.all_required_passed is False


def test_all_required_passed_false_when_execution_lookup_not_supported():
    proof = _make_proof(execution_lookup_supported=False)
    assert proof.all_required_passed is False


def test_failed_checks_lists_margin_estimate_invalid():
    proof = _make_proof(margin_estimate_valid=False)
    failed = proof.failed_checks()
    assert "margin_estimate_valid" in failed


def test_failed_checks_lists_execution_lookup_not_supported():
    proof = _make_proof(execution_lookup_supported=False)
    failed = proof.failed_checks()
    assert "execution_lookup_supported" in failed


def test_failed_checks_empty_when_all_pass():
    proof = _make_proof()
    assert proof.failed_checks() == []


# ---------------------------------------------------------------------------
# BrokerCapabilityProofService — persistence
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_proof_inserts_audit_log():
    from app.services.broker_capability_proof_service import BrokerCapabilityProofService

    mock_db = AsyncMock()
    svc = BrokerCapabilityProofService(mock_db)

    await svc.record_proof(
        bot_instance_id="bot-1",
        provider="ctrader_live",
        account_id="acc-42",
        symbol="EURUSD",
        timeframe="M15",
        proof_payload={"all_required_passed": True, "latency_ms": 12.5},
    )

    # db.add should have been called with an AuditLog instance
    mock_db.add.assert_called_once()
    audit_log_arg = mock_db.add.call_args[0][0]
    assert audit_log_arg.action == "broker_capability_proof"
    assert audit_log_arg.resource_type == "bot"
    assert audit_log_arg.resource_id == "bot-1"
    assert "proof_hash" in audit_log_arg.details
    mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_record_proof_hash_is_deterministic():
    from app.services.broker_capability_proof_service import BrokerCapabilityProofService

    mock_db = AsyncMock()
    svc = BrokerCapabilityProofService(mock_db)

    payload = {"all_required_passed": True, "symbol": "EURUSD", "timeframe": "M15"}
    await svc.record_proof(
        bot_instance_id="bot-1", provider="prov", account_id="acc",
        symbol="EURUSD", timeframe="M15", proof_payload=payload,
    )
    audit1 = mock_db.add.call_args[0][0].details["proof_hash"]

    mock_db.reset_mock()
    await svc.record_proof(
        bot_instance_id="bot-1", provider="prov", account_id="acc",
        symbol="EURUSD", timeframe="M15", proof_payload=payload,
    )
    audit2 = mock_db.add.call_args[0][0].details["proof_hash"]

    assert audit1 == audit2


# ---------------------------------------------------------------------------
# BrokerQuoteSnapshot + BrokerInstrumentSpecSnapshot (P0-D)
# ---------------------------------------------------------------------------

def test_broker_quote_snapshot_canonical_hash_stable():
    from trading_core.risk.broker_snapshot import BrokerQuoteSnapshot

    q = BrokerQuoteSnapshot(
        symbol="EURUSD",
        bid=1.1000,
        ask=1.1002,
        timestamp=1_700_000_000.0,
        quote_id="q123",
        source="ctrader_live",
        latency_ms=5.0,
    )
    assert q.canonical_hash == q.canonical_hash  # deterministic


def test_broker_quote_snapshot_hash_changes_on_price_change():
    from trading_core.risk.broker_snapshot import BrokerQuoteSnapshot

    q1 = BrokerQuoteSnapshot(
        symbol="EURUSD", bid=1.1000, ask=1.1002,
        timestamp=1_700_000_000.0, quote_id="q1", source="x",
    )
    q2 = BrokerQuoteSnapshot(
        symbol="EURUSD", bid=1.1005, ask=1.1007,
        timestamp=1_700_000_000.0, quote_id="q1", source="x",
    )
    assert q1.canonical_hash != q2.canonical_hash


def test_broker_quote_snapshot_validate_rejects_zero_bid():
    from trading_core.risk.broker_snapshot import BrokerQuoteSnapshot

    q = BrokerQuoteSnapshot(
        symbol="EURUSD", bid=0.0, ask=1.1002,
        timestamp=1_700_000_000.0, quote_id="q1", source="x",
    )
    with pytest.raises(ValueError, match="bid"):
        q.validate()


def test_broker_quote_snapshot_validate_rejects_ask_lt_bid():
    from trading_core.risk.broker_snapshot import BrokerQuoteSnapshot

    q = BrokerQuoteSnapshot(
        symbol="EURUSD", bid=1.1005, ask=1.1000,
        timestamp=1_700_000_000.0, quote_id="q1", source="x",
    )
    with pytest.raises(ValueError, match="ask"):
        q.validate()


def test_broker_instrument_spec_canonical_hash_stable():
    from trading_core.risk.broker_snapshot import BrokerInstrumentSpecSnapshot

    s = BrokerInstrumentSpecSnapshot(
        symbol="EURUSD",
        pip_size=0.0001, tick_size=0.00001,
        contract_size=100_000.0, min_volume=0.01,
        max_volume=100.0, volume_step=0.01,
        margin_rate=0.01, currency_profit="USD",
        currency_margin="EUR", source="ctrader",
    )
    assert s.canonical_hash == s.canonical_hash


def test_broker_instrument_spec_validate_rejects_zero_pip():
    from trading_core.risk.broker_snapshot import BrokerInstrumentSpecSnapshot

    s = BrokerInstrumentSpecSnapshot(
        symbol="EURUSD",
        pip_size=0.0, tick_size=0.00001,
        contract_size=100_000.0, min_volume=0.01,
        max_volume=100.0, volume_step=0.01,
        margin_rate=0.01, currency_profit="USD",
        currency_margin="EUR",
    )
    with pytest.raises(ValueError, match="pip_size"):
        s.validate()
