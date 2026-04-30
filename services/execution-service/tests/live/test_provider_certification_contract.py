from __future__ import annotations

from execution_service.providers.base import BrokerCapabilityProof


def _full_ok_proof() -> BrokerCapabilityProof:
    return BrokerCapabilityProof(
        provider="ctrader_live",
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
    )


def test_provider_certification_contract_all_required_passed_true_when_all_checks_true():
    proof = _full_ok_proof()
    assert proof.all_required_passed is True
    assert proof.failed_checks() == []


def test_provider_certification_contract_all_required_passed_false_when_any_check_false():
    proof = _full_ok_proof()
    proof.execution_lookup_supported = False
    assert proof.all_required_passed is False
    assert "execution_lookup_supported" in proof.failed_checks()


def test_provider_certification_contract_failed_checks_reports_multiple_missing_checks():
    proof = _full_ok_proof()
    proof.account_id_match = False
    proof.margin_estimate_valid = False

    failed = proof.failed_checks()
    assert "account_id_match" in failed
    assert "margin_estimate_valid" in failed
    assert len(failed) >= 2
