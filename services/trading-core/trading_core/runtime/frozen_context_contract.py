from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FrozenContextValidationResult:
    ok: bool
    reason: str = "ok"


def validate_frozen_context_bindings(*, request: Any, context: Any, provider_name: str) -> FrozenContextValidationResult:
    """Validate immutable live bindings between runtime context and execution request."""
    if str(getattr(context, "bot_instance_id", "") or "") == "":
        return FrozenContextValidationResult(False, "missing_bot_instance_id")
    if str(getattr(context, "idempotency_key", "") or "") == "":
        return FrozenContextValidationResult(False, "missing_idempotency_key")
    if str(getattr(context, "brain_cycle_id", "") or "") == "":
        return FrozenContextValidationResult(False, "missing_brain_cycle_id")
    if str(getattr(context, "account_id", "") or "") == "":
        return FrozenContextValidationResult(False, "missing_account_id")
    if str(getattr(context, "broker_name", "") or "").lower() != str(provider_name or "").lower():
        return FrozenContextValidationResult(False, "broker_name_mismatch")
    if str(getattr(context, "policy_version", "") or "") == "":
        return FrozenContextValidationResult(False, "missing_policy_version")

    gate_ctx = getattr(context, "gate_context", {}) or {}
    if str(gate_ctx.get("schema_version", "") or "") != "gate_context_v2":
        return FrozenContextValidationResult(False, "invalid_gate_context_schema_version")
    for key in ("symbol", "side", "requested_volume", "idempotency_key"):
        if str(gate_ctx.get(key, "") or "") == "":
            return FrozenContextValidationResult(False, f"missing_gate_context_{key}")

    for key in ("account_id", "broker_name", "policy_version"):
        if str(gate_ctx.get(key, "") or "") == "":
            return FrozenContextValidationResult(False, f"missing_gate_context_{key}")

    for key in (
        "policy_version_id",
        "quote_id",
        "quote_timestamp",
        "instrument_spec_hash",
        "broker_snapshot_hash",
        "broker_account_snapshot_hash",
        "risk_context_hash",
        "policy_hash",
    ):
        if str(gate_ctx.get(key, "") or "") == "":
            return FrozenContextValidationResult(False, f"missing_gate_context_{key}")

    ctx_idempotency = str(getattr(context, "idempotency_key", "") or "")
    gate_idempotency = str(gate_ctx.get("idempotency_key", "") or "")
    if ctx_idempotency != gate_idempotency:
        return FrozenContextValidationResult(False, "idempotency_key_mismatch_in_gate")

    req_symbol = str(getattr(request, "symbol", "") or "").upper()
    ctx_symbol = str(gate_ctx.get("symbol") or "").upper()
    if req_symbol != ctx_symbol:
        return FrozenContextValidationResult(False, "symbol_mismatch")

    req_side = str(getattr(request, "side", "") or "").lower()
    ctx_side = str(gate_ctx.get("side") or "").lower()
    if req_side != ctx_side:
        return FrozenContextValidationResult(False, "side_mismatch")

    req_volume = float(getattr(request, "volume", 0.0) or 0.0)
    frozen_volume = float(gate_ctx.get("approved_volume", gate_ctx.get("requested_volume", 0.0)) or 0.0)
    if abs(req_volume - frozen_volume) > 1e-9:
        return FrozenContextValidationResult(False, "requested_volume_mismatch")

    gate_account_id = str(gate_ctx.get("account_id", "") or "")
    if str(getattr(context, "account_id", "") or "") != gate_account_id:
        return FrozenContextValidationResult(False, "account_id_mismatch_in_gate")

    gate_broker = str(gate_ctx.get("broker_name", "") or "")
    if gate_broker.lower() != str(provider_name or "").lower():
        return FrozenContextValidationResult(False, "broker_name_mismatch_in_gate")

    gate_policy_version = str(gate_ctx.get("policy_version", "") or "")
    if gate_policy_version != str(getattr(context, "policy_version", "") or ""):
        return FrozenContextValidationResult(False, "policy_version_mismatch_in_gate")

    if str(gate_ctx.get("policy_status", "") or "active").lower() != "active":
        return FrozenContextValidationResult(False, "policy_status_not_active")

    req_type = str(getattr(request, "order_type", "") or "market").lower()
    if str(getattr(context, "order_type", "market") or "market").lower() != req_type:
        return FrozenContextValidationResult(False, "order_type_mismatch")

    req_price = float(getattr(request, "price", 0.0) or 0.0)
    ctx_price = float(getattr(context, "entry_price", 0.0) or 0.0)
    deviation = abs(req_price - ctx_price)
    if req_price > 0 and ctx_price > 0:
        deviation_bps = (deviation / req_price) * 10000.0
        max_dev_bps = float(gate_ctx.get("max_price_deviation_bps", 20.0) or 20.0)
        if deviation_bps > max_dev_bps:
            return FrozenContextValidationResult(False, "entry_price_out_of_band")

    req_sl = float(getattr(request, "stop_loss", 0.0) or 0.0)
    ctx_sl = float(getattr(context, "stop_loss", 0.0) or 0.0)
    if req_sl > 0 and ctx_sl > 0 and abs(req_sl - ctx_sl) > 1e-9:
        return FrozenContextValidationResult(False, "stop_loss_mismatch")

    req_tp = float(getattr(request, "take_profit", 0.0) or 0.0)
    ctx_tp = float(getattr(context, "take_profit", 0.0) or 0.0)
    if req_tp > 0 and ctx_tp > 0 and abs(req_tp - ctx_tp) > 1e-9:
        return FrozenContextValidationResult(False, "take_profit_mismatch")

    stored_hash = str(getattr(context, "context_hash", "") or "")
    if stored_hash:
        from trading_core.runtime.pre_execution_gate import hash_gate_context

        computed_hash = hash_gate_context(gate_ctx)
        if computed_hash != stored_hash:
            return FrozenContextValidationResult(False, "gate_context_hash_mismatch")

    return FrozenContextValidationResult(True, "ok")
