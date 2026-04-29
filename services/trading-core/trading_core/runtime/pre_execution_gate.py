"""services/trading-core/trading_core/runtime/pre_execution_gate.py"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict

from trading_core.risk.daily_profit_policy import resolve_daily_take_profit_target


@dataclass(frozen=True)
class GateResult:
    action: str  # ALLOW | SKIP | BLOCK
    reason: str
    severity: str = "info"
    lock_scope: str = "none"
    operator_action: str = "none"
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GateContext:
    provider_mode: str
    runtime_mode: str
    broker_connected: bool
    market_data_ok: bool
    data_age_seconds: float
    spread_pips: float
    confidence: float
    rr: float
    open_positions: int
    daily_profit_amount: float
    daily_loss_pct: float
    consecutive_losses: int
    daily_locked: bool = False
    daily_lock_reason: str = ""
    kill_switch: bool = False
    idempotency_exists: bool = False
    margin_usage_pct: float = 0.0
    free_margin_after_order: float = 0.0
    account_exposure_pct: float = 0.0
    symbol_exposure_pct: float = 0.0
    correlated_usd_exposure_pct: float = 0.0
    portfolio_daily_loss_pct: float = 0.0
    portfolio_open_positions: int = 0
    portfolio_kill_switch: bool = False
    policy_version_approved: bool = True
    new_orders_paused: bool = False
    stop_loss: float = 0.0
    max_loss_amount_if_sl_hit: float = 0.0
    requested_volume: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, context: Dict[str, Any]) -> "GateContext":
        return cls(
            provider_mode=str(context.get("provider_mode", "stub")),
            runtime_mode=str(context.get("runtime_mode", "paper")),
            broker_connected=bool(context.get("broker_connected", False)),
            market_data_ok=bool(context.get("market_data_ok", False)),
            data_age_seconds=float(context.get("data_age_seconds", 0.0) or 0.0),
            spread_pips=float(context.get("spread_pips", 0.0) or 0.0),
            confidence=float(context.get("confidence", 0.0) or 0.0),
            rr=float(context.get("rr", 0.0) or 0.0),
            open_positions=int(context.get("open_positions", 0) or 0),
            daily_profit_amount=float(context.get("daily_profit_amount", 0.0) or 0.0),
            daily_loss_pct=float(context.get("daily_loss_pct", 0.0) or 0.0),
            consecutive_losses=int(context.get("consecutive_losses", 0) or 0),
            daily_locked=bool(context.get("daily_locked", False)),
            daily_lock_reason=str(context.get("daily_lock_reason", "") or ""),
            kill_switch=bool(context.get("kill_switch", False)),
            idempotency_exists=bool(context.get("idempotency_exists", False)),
            margin_usage_pct=float(context.get("margin_usage_pct", 0.0) or 0.0),
            free_margin_after_order=float(context.get("free_margin_after_order", 0.0) or 0.0),
            account_exposure_pct=float(context.get("account_exposure_pct", 0.0) or 0.0),
            symbol_exposure_pct=float(context.get("symbol_exposure_pct", 0.0) or 0.0),
            correlated_usd_exposure_pct=float(context.get("correlated_usd_exposure_pct", 0.0) or 0.0),
            portfolio_daily_loss_pct=float(context.get("portfolio_daily_loss_pct", 0.0) or 0.0),
            portfolio_open_positions=int(context.get("portfolio_open_positions", 0) or 0),
            portfolio_kill_switch=bool(context.get("portfolio_kill_switch", False)),
            policy_version_approved=bool(context.get("policy_version_approved", True)),
            new_orders_paused=bool(context.get("new_orders_paused", False)),
            stop_loss=float(context.get("stop_loss", 0.0) or 0.0),
            max_loss_amount_if_sl_hit=float(context.get("max_loss_amount_if_sl_hit", 0.0) or 0.0),
            requested_volume=float(context.get("requested_volume", 0.0) or 0.0),
        )


def canonicalize_gate_context(context: Dict[str, Any]) -> Dict[str, Any]:
    return GateContext.from_dict(context).to_dict()


def hash_gate_context(context: Dict[str, Any]) -> str:
    canonical = canonicalize_gate_context(context)
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class PreExecutionGate:
    """Final fail-closed gate immediately before broker order placement."""

    def __init__(self, policy: Dict[str, Any]) -> None:
        self.policy = policy or {}

    def _daily_take_profit_target(self, context: Dict[str, Any]) -> float:
        starting_equity = float(context.get("starting_equity", 0.0) or 0.0)
        daily_profit_amount = float(context.get("daily_profit_amount", 0.0) or 0.0)
        policy = self.policy
        if isinstance(policy, dict) and "daily_take_profit" not in policy:
            # Backward compatibility for flat policy keys used by existing tests/config.
            mode = str(policy.get("daily_take_profit_mode", "fixed_amount") or "fixed_amount")
            configured_amount = policy.get("daily_take_profit_amount", None)
            configured_pct = policy.get("daily_take_profit_pct", None)
            if configured_amount is not None or configured_pct is not None:
                policy = {
                    **policy,
                    "daily_take_profit": {
                        "enabled": True,
                        "mode": mode,
                        "daily_take_profit_amount": float(configured_amount or 0.0),
                        "daily_take_profit_pct": float(configured_pct or 0.0),
                    },
                }
        return resolve_daily_take_profit_target(
            policy,
            starting_equity=starting_equity,
            daily_profit_amount=daily_profit_amount,
        )

    def evaluate(self, context: Dict[str, Any]) -> GateResult:
        # kill_switch can come from context (runtime signal) or policy (static config)
        if context.get("kill_switch") is True or self.policy.get("kill_switch") is True:
            return GateResult("BLOCK", "kill_switch_enabled", severity="critical", lock_scope="bot", operator_action="reset_required")
        if context.get("portfolio_kill_switch") is True:
            return GateResult("BLOCK", "portfolio_kill_switch_enabled", severity="critical", lock_scope="portfolio", operator_action="reset_required")
        # explicit daily_locked from DailyTradingState
        if context.get("daily_locked") is True:
            lock_reason = str(context.get("daily_lock_reason") or "daily_locked")
            return GateResult("BLOCK", lock_reason, severity="warning", lock_scope="bot", operator_action="reset_required")
        # P0.6: new_orders_paused by daily lock controller
        if context.get("new_orders_paused") is True:
            return GateResult("BLOCK", "new_orders_paused", severity="warning", lock_scope="bot", operator_action="reset_required")
        if context.get("runtime_mode") == "live" and not context.get("broker_connected"):
            return GateResult("BLOCK", "broker_not_connected", severity="critical", operator_action="broker_check")
        # stub/degraded provider only blocks in live mode
        if context.get("runtime_mode") == "live" and context.get("provider_mode") in {"stub", "degraded", "unavailable"}:
            return GateResult("BLOCK", "provider_not_live_capable", severity="critical", operator_action="broker_check")
        if context.get("runtime_mode") == "live" and context.get("policy_version_approved") is False:
            return GateResult("BLOCK", "policy_version_unapproved", severity="critical", operator_action="review")
        # P0.3: SL required in live mode when explicitly configured or provided in context.
        # This keeps backwards compatibility for legacy tests/contexts that do not include stop_loss.
        if context.get("runtime_mode") == "live":
            has_stop_loss_field = "stop_loss" in context
            sl_required = bool(self.policy.get("stop_loss_required_in_live", False)) or has_stop_loss_field
            if sl_required:
                sl = float(context.get("stop_loss", 0.0) or 0.0)
                if sl <= 0:
                    return GateResult("BLOCK", "stop_loss_required_in_live", severity="critical", operator_action="review")
        if context.get("news_blackout_active") is True:
            return GateResult("BLOCK", "news_blackout_active", severity="warning", operator_action="review")
        if context.get("session_allowed") is False:
            return GateResult("BLOCK", "session_not_allowed", severity="warning", operator_action="review")
        if float(context.get("broker_clock_drift_seconds", 0.0) or 0.0) > float(self.policy.get("max_broker_clock_drift_seconds", 5.0)):
            return GateResult("BLOCK", "broker_clock_drift_too_high", severity="critical", operator_action="broker_check")
        if context.get("market_data_ok") is False:
            return GateResult("BLOCK", "market_data_invalid", severity="critical", operator_action="broker_check")
        if float(context.get("data_age_seconds", 0)) > float(self.policy.get("max_data_age_seconds", 30)):
            return GateResult("BLOCK", "market_data_stale", severity="warning", operator_action="review")
        if float(context.get("daily_loss_pct", 0)) >= float(self.policy.get("max_daily_loss_pct", 5)):
            return GateResult("BLOCK", "daily_loss_limit_hit", severity="critical", lock_scope="bot", operator_action="reset_required")
        if float(context.get("portfolio_daily_loss_pct", 0.0) or 0.0) >= float(self.policy.get("max_portfolio_daily_loss_pct", 10.0)):
            return GateResult("BLOCK", "portfolio_daily_loss_limit_hit", severity="critical", lock_scope="portfolio", operator_action="reset_required")
        if float(context.get("daily_profit_amount", 0)) >= self._daily_take_profit_target(context):
            return GateResult("BLOCK", "daily_take_profit_hit", severity="warning", lock_scope="bot", operator_action="reset_required")
        if int(context.get("consecutive_losses", 0)) >= int(self.policy.get("max_consecutive_losses", 4)):
            return GateResult("BLOCK", "consecutive_loss_limit_hit", severity="warning", operator_action="review")
        if float(context.get("margin_usage_pct", 0.0) or 0.0) > float(self.policy.get("max_margin_usage_pct", 80.0)):
            return GateResult("BLOCK", "margin_usage_too_high", severity="critical", operator_action="broker_check")
        # P0.3: Max risk amount per trade check
        max_risk_amount = float(self.policy.get("max_risk_amount_per_trade", 0.0) or 0.0)
        max_loss = float(context.get("max_loss_amount_if_sl_hit", 0.0) or 0.0)
        if max_risk_amount > 0 and max_loss > max_risk_amount:
            return GateResult("BLOCK", "max_risk_amount_per_trade_exceeded", severity="critical", operator_action="review")
        # P0.3: Lot limit validation from policy
        requested_volume = float(context.get("requested_volume", 0.0) or 0.0)
        if requested_volume > 0:
            max_lot = float(self.policy.get("max_lot_per_trade", 0.0) or 0.0)
            min_lot = float(self.policy.get("min_lot_per_trade", 0.0) or 0.0)
            if max_lot > 0 and requested_volume > max_lot:
                return GateResult("BLOCK", "lot_size_above_policy_max", severity="critical", operator_action="review")
            if min_lot > 0 and requested_volume < min_lot:
                return GateResult("BLOCK", "lot_size_below_policy_min", severity="critical", operator_action="review")
        if float(context.get("account_exposure_pct", 0.0) or 0.0) > float(self.policy.get("max_account_exposure_pct", 50.0)):
            return GateResult("BLOCK", "account_exposure_too_high", severity="warning", operator_action="review")
        if float(context.get("symbol_exposure_pct", 0.0) or 0.0) > float(self.policy.get("max_symbol_exposure_pct", 25.0)):
            return GateResult("BLOCK", "symbol_exposure_too_high", severity="warning", operator_action="review")
        if float(context.get("correlated_usd_exposure_pct", 0.0) or 0.0) > float(self.policy.get("max_correlated_usd_exposure_pct", 35.0)):
            return GateResult("BLOCK", "correlated_exposure_too_high", severity="warning", operator_action="review")
        if float(context.get("slippage_pips", 0.0) or 0.0) > float(self.policy.get("max_slippage_pips", 3.0)):
            return GateResult("BLOCK", "slippage_too_high", severity="warning", operator_action="review")
        if float(context.get("free_margin_after_order", 1e18) or 1e18) < float(self.policy.get("min_free_margin_after_order", 0.0)):
            return GateResult("BLOCK", "free_margin_after_order_too_low", severity="critical", operator_action="broker_check")
        if float(context.get("spread_pips", 0)) > float(self.policy.get("max_spread_pips", 2.0)):
            return GateResult("BLOCK", "spread_too_high", severity="warning", operator_action="review")
        if int(context.get("open_positions", 0)) >= int(self.policy.get("max_open_positions", 3)):
            return GateResult("BLOCK", "max_open_positions_hit", severity="warning", operator_action="review")
        if int(context.get("portfolio_open_positions", 0) or 0) >= int(self.policy.get("max_portfolio_open_positions", 12)):
            return GateResult("BLOCK", "max_portfolio_open_positions_hit", severity="warning", operator_action="review")
        if context.get("idempotency_exists") is True:
            return GateResult("BLOCK", "duplicate_order_blocked", severity="warning", operator_action="review")
        if float(context.get("confidence", 0)) < float(self.policy.get("min_confidence", 0.65)):
            return GateResult("SKIP", "confidence_too_low", severity="info")
        if float(context.get("rr", 0)) < float(self.policy.get("min_rr", 1.5)):
            return GateResult("SKIP", "rr_too_low", severity="info")
        return GateResult("ALLOW", "ok")
