"""Proposed file: services/trading-core/trading_core/runtime/pre_execution_gate.py"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass(frozen=True)
class GateResult:
    action: str  # ALLOW | SKIP | BLOCK
    reason: str
    details: Dict[str, Any] = field(default_factory=dict)


class PreExecutionGate:
    """Final fail-closed gate immediately before broker order placement."""

    def __init__(self, policy: Dict[str, Any]) -> None:
        self.policy = policy or {}

    def evaluate(self, context: Dict[str, Any]) -> GateResult:
        if self.policy.get("kill_switch") is True:
            return GateResult("BLOCK", "kill_switch_enabled")
        if context.get("runtime_mode") == "live" and not context.get("broker_connected"):
            return GateResult("BLOCK", "broker_not_connected")
        if context.get("provider_mode") in {"stub", "degraded", "unavailable"}:
            return GateResult("BLOCK", "provider_not_live_capable")
        if context.get("market_data_ok") is False:
            return GateResult("BLOCK", "market_data_invalid")
        if float(context.get("data_age_seconds", 0)) > float(self.policy.get("max_data_age_seconds", 30)):
            return GateResult("BLOCK", "market_data_stale")
        if float(context.get("daily_loss_pct", 0)) >= float(self.policy.get("max_daily_loss_pct", 5)):
            return GateResult("BLOCK", "daily_loss_limit_hit")
        if float(context.get("daily_profit_amount", 0)) >= float(self.policy.get("daily_take_profit_amount", 10**18)):
            return GateResult("BLOCK", "daily_take_profit_hit")
        if int(context.get("consecutive_losses", 0)) >= int(self.policy.get("max_consecutive_losses", 4)):
            return GateResult("BLOCK", "consecutive_loss_limit_hit")
        if float(context.get("spread_pips", 0)) > float(self.policy.get("max_spread_pips", 2.0)):
            return GateResult("BLOCK", "spread_too_high")
        if int(context.get("open_positions", 0)) >= int(self.policy.get("max_open_positions", 3)):
            return GateResult("BLOCK", "max_open_positions_hit")
        if context.get("idempotency_exists") is True:
            return GateResult("BLOCK", "duplicate_order_blocked")
        if float(context.get("confidence", 0)) < float(self.policy.get("min_confidence", 0.65)):
            return GateResult("SKIP", "confidence_too_low")
        if float(context.get("rr", 0)) < float(self.policy.get("min_rr", 1.5)):
            return GateResult("SKIP", "rr_too_low")
        return GateResult("ALLOW", "ok")
