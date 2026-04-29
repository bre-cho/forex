from __future__ import annotations

from dataclasses import dataclass


_ALLOWED: dict[str, set[str]] = {
    "INTENT_CREATED": {"GATE_ALLOWED", "GATE_BLOCKED"},
    "GATE_ALLOWED": {"RESERVED"},
    "RESERVED": {"SUBMITTING", "SUBMITTED"},
    "SUBMITTING": {"SUBMITTED", "ACKED", "FILLED", "PARTIAL", "REJECTED", "UNKNOWN", "ACK_TIMEOUT"},
    "SUBMITTED": {"ACKED", "FILLED", "PARTIAL", "REJECTED", "UNKNOWN", "CANCEL_REQUESTED", "CANCELLED", "EXPIRED", "REPLACE_REQUESTED"},
    "ACKED": {"FILLED", "PARTIAL", "UNKNOWN", "ACK_TIMEOUT", "CANCEL_REQUESTED", "CANCELLED", "EXPIRED", "REPLACE_REQUESTED"},
    "ACK_TIMEOUT": {"UNKNOWN", "RECONCILING"},
    "PARTIAL": {"OPEN_POSITION_VERIFIED", "UNKNOWN", "CLOSED", "CANCEL_REQUESTED", "CANCELLED"},
    "CANCEL_REQUESTED": {"CANCELLED", "FILLED", "PARTIAL", "REJECTED", "EXPIRED"},
    "REPLACE_REQUESTED": {"REPLACED", "REJECTED", "CANCELLED"},
    "REPLACED": {"SUBMITTED", "ACKED", "FILLED", "PARTIAL", "REJECTED", "UNKNOWN", "CANCEL_REQUESTED", "EXPIRED"},
    "UNKNOWN": {"RECONCILING"},
    "RECONCILING": {"FILLED", "REJECTED", "FAILED_NEEDS_OPERATOR"},
    "FILLED": {"OPEN_POSITION_VERIFIED", "POSITION_VERIFY_PENDING", "CLOSED"},
    "POSITION_VERIFY_PENDING": {"OPEN_POSITION_VERIFIED", "UNKNOWN", "FILLED"},
    "OPEN_POSITION_VERIFIED": {"PARTIAL", "CLOSED"},
    "CANCELLED": set(),
    "EXPIRED": set(),
    "FAILED_NEEDS_OPERATOR": set(),
}


@dataclass(frozen=True)
class TransitionDecision:
    ok: bool
    reason: str = ""


def validate_transition(current_state: str | None, next_state: str) -> TransitionDecision:
    src = str(current_state or "INTENT_CREATED").upper()
    dst = str(next_state or "").upper()
    if not dst:
        return TransitionDecision(False, "missing_next_state")
    allowed = _ALLOWED.get(src, set())
    if dst in allowed:
        return TransitionDecision(True, "ok")
    if src == dst:
        return TransitionDecision(True, "idempotent")
    return TransitionDecision(False, f"invalid_transition:{src}->{dst}")
