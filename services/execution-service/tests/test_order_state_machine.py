from __future__ import annotations

from execution_service.order_state_machine import validate_transition


def test_validate_transition_allows_happy_path() -> None:
    assert validate_transition("INTENT_CREATED", "GATE_ALLOWED").ok is True
    assert validate_transition("GATE_ALLOWED", "RESERVED").ok is True
    assert validate_transition("RESERVED", "SUBMITTED").ok is True
    assert validate_transition("SUBMITTED", "FILLED").ok is True


def test_validate_transition_blocks_invalid_hop() -> None:
    decision = validate_transition("GATE_ALLOWED", "FILLED")
    assert decision.ok is False
    assert "invalid_transition" in decision.reason
