from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ContractResult:
    ok: bool
    reason: str = "ok"
    missing: list[str] = field(default_factory=list)


def _missing(envelope: dict[str, Any], keys: list[str]) -> list[str]:
    out: list[str] = []
    for key in keys:
        value = envelope.get(key)
        if value is None:
            out.append(key)
            continue
        if isinstance(value, str) and not value.strip():
            out.append(key)
    return out


def validate_order_contract(mode: str, envelope: dict[str, Any]) -> ContractResult:
    normalized = str(mode or "paper").lower()
    core = ["signal_id", "symbol", "side", "volume", "order_type"]
    missing_core = _missing(envelope, core)
    if missing_core:
        return ContractResult(False, "missing_core_fields", missing_core)

    if normalized in {"paper", "backtest"}:
        return ContractResult(True)

    # demo/live must carry governance context and idempotency trace.
    req = ["idempotency_key", "brain_cycle_id", "pre_execution_context"]
    missing = _missing(envelope, req)
    if missing:
        return ContractResult(False, "missing_governance_fields", missing)

    if normalized == "demo":
        return ContractResult(True)

    # live additionally requires receipt-grade evidence when marked success.
    success = bool(envelope.get("success", False))
    if success:
        receipt_req = ["submit_status", "fill_status", "broker_order_id"]
        missing_receipt = _missing(envelope, receipt_req)
        if missing_receipt:
            return ContractResult(False, "missing_live_receipt_fields", missing_receipt)
    return ContractResult(True)
