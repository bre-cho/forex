from __future__ import annotations

import hashlib
import json
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

    # P0.3: validate idempotency_key matches between context and gate_context
    ctx_idempotency = str(getattr(context, "idempotency_key", "") or "")
    gate_idempotency = str((getattr(context, "gate_context", {}) or {}).get("idempotency_key", "") or "")
    if gate_idempotency and ctx_idempotency and ctx_idempotency != gate_idempotency:
        return FrozenContextValidationResult(False, "idempotency_key_mismatch_in_gate")

    req_symbol = str(getattr(request, "symbol", "") or "").upper()
    ctx_symbol = str((getattr(context, "gate_context", {}) or {}).get("symbol") or req_symbol).upper()
    if req_symbol != ctx_symbol:
        return FrozenContextValidationResult(False, "symbol_mismatch")

    req_side = str(getattr(request, "side", "") or "").lower()
    ctx_side = str((getattr(context, "gate_context", {}) or {}).get("side") or req_side).lower()
    if req_side != ctx_side:
        return FrozenContextValidationResult(False, "side_mismatch")

    req_volume = float(getattr(request, "volume", 0.0) or 0.0)
    frozen_volume = float((getattr(context, "gate_context", {}) or {}).get("requested_volume", req_volume) or req_volume)
    if abs(req_volume - frozen_volume) > 1e-9:
        return FrozenContextValidationResult(False, "requested_volume_mismatch")

    req_type = str(getattr(request, "order_type", "") or "market").lower()
    if str(getattr(context, "order_type", "market") or "market").lower() != req_type:
        return FrozenContextValidationResult(False, "order_type_mismatch")

    req_price = float(getattr(request, "price", 0.0) or 0.0)
    ctx_price = float(getattr(context, "entry_price", 0.0) or 0.0)
    if req_price > 0 and ctx_price > 0 and abs(req_price - ctx_price) > max(1e-9, req_price * 0.05):
        return FrozenContextValidationResult(False, "entry_price_out_of_band")

    req_sl = float(getattr(request, "stop_loss", 0.0) or 0.0)
    ctx_sl = float(getattr(context, "stop_loss", 0.0) or 0.0)
    if req_sl > 0 and ctx_sl > 0 and abs(req_sl - ctx_sl) > 1e-9:
        return FrozenContextValidationResult(False, "stop_loss_mismatch")

    req_tp = float(getattr(request, "take_profit", 0.0) or 0.0)
    ctx_tp = float(getattr(context, "take_profit", 0.0) or 0.0)
    if req_tp > 0 and ctx_tp > 0 and abs(req_tp - ctx_tp) > 1e-9:
        return FrozenContextValidationResult(False, "take_profit_mismatch")

    # P0.3: verify context_hash matches gate_context if both present
    stored_hash = str(getattr(context, "context_hash", "") or "")
    gate_ctx = getattr(context, "gate_context", {}) or {}
    if stored_hash and gate_ctx:
        from trading_core.runtime.pre_execution_gate import hash_gate_context
        computed_hash = hash_gate_context(gate_ctx)
        if computed_hash != stored_hash:
            return FrozenContextValidationResult(False, "gate_context_hash_mismatch")

    return FrozenContextValidationResult(True, "ok")
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

    req_symbol = str(getattr(request, "symbol", "") or "").upper()
    ctx_symbol = str((getattr(context, "gate_context", {}) or {}).get("symbol") or req_symbol).upper()
    if req_symbol != ctx_symbol:
        return FrozenContextValidationResult(False, "symbol_mismatch")

    req_side = str(getattr(request, "side", "") or "").lower()
    ctx_side = str((getattr(context, "gate_context", {}) or {}).get("side") or req_side).lower()
    if req_side != ctx_side:
        return FrozenContextValidationResult(False, "side_mismatch")

    req_volume = float(getattr(request, "volume", 0.0) or 0.0)
    frozen_volume = float((getattr(context, "gate_context", {}) or {}).get("requested_volume", req_volume) or req_volume)
    if abs(req_volume - frozen_volume) > 1e-9:
        return FrozenContextValidationResult(False, "requested_volume_mismatch")

    req_type = str(getattr(request, "order_type", "") or "market").lower()
    if str(getattr(context, "order_type", "market") or "market").lower() != req_type:
        return FrozenContextValidationResult(False, "order_type_mismatch")

    req_price = float(getattr(request, "price", 0.0) or 0.0)
    ctx_price = float(getattr(context, "entry_price", 0.0) or 0.0)
    if req_price > 0 and ctx_price > 0 and abs(req_price - ctx_price) > max(1e-9, req_price * 0.05):
        return FrozenContextValidationResult(False, "entry_price_out_of_band")

    req_sl = float(getattr(request, "stop_loss", 0.0) or 0.0)
    ctx_sl = float(getattr(context, "stop_loss", 0.0) or 0.0)
    if req_sl > 0 and ctx_sl > 0 and abs(req_sl - ctx_sl) > 1e-9:
        return FrozenContextValidationResult(False, "stop_loss_mismatch")

    req_tp = float(getattr(request, "take_profit", 0.0) or 0.0)
    ctx_tp = float(getattr(context, "take_profit", 0.0) or 0.0)
    if req_tp > 0 and ctx_tp > 0 and abs(req_tp - ctx_tp) > 1e-9:
        return FrozenContextValidationResult(False, "take_profit_mismatch")

    return FrozenContextValidationResult(True, "ok")
