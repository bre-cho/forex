"""
Trading Runtime Governance — AI_SYSTEM_FULL validation layer for live/paper bots.

This guard does not replace strategy logic. It wraps runtime actions with
production checks: data availability, broker health, confidence floor,
risk limits, and missing execution fields.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional


class GovernanceDecision(str, Enum):
    ALLOW = "allow"
    REVIEW = "review"
    BLOCK = "block"


@dataclass(frozen=True)
class GovernanceIssue:
    code: str
    message: str
    severity: str = "warning"


@dataclass
class GovernanceReport:
    decision: GovernanceDecision
    issues: List[GovernanceIssue] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.decision == GovernanceDecision.ALLOW

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision.value,
            "allowed": self.allowed,
            "issues": [issue.__dict__ for issue in self.issues],
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class GovernancePolicy:
    min_signal_confidence: float = 0.55
    max_daily_loss_pct: float = 3.0
    require_broker_health_for_live: bool = True
    require_sl_tp_for_orders: bool = True
    block_on_empty_market_data: bool = True


class TradingRuntimeGuard:
    def __init__(self, policy: Optional[GovernancePolicy] = None) -> None:
        self.policy = policy or GovernancePolicy()

    def validate_market_data(self, df: Any) -> GovernanceReport:
        if df is None:
            return self._block("market_data_missing", "Market data is None")
        if getattr(df, "empty", False):
            decision = GovernanceDecision.BLOCK if self.policy.block_on_empty_market_data else GovernanceDecision.REVIEW
            return GovernanceReport(
                decision=decision,
                issues=[GovernanceIssue("market_data_empty", "Market data frame is empty", "error")],
            )
        missing = [c for c in ("open", "high", "low", "close") if c not in getattr(df, "columns", [])]
        if missing:
            return GovernanceReport(
                decision=GovernanceDecision.BLOCK,
                issues=[GovernanceIssue("market_data_schema", f"Missing columns: {missing}", "error")],
            )
        return GovernanceReport(GovernanceDecision.ALLOW)

    def validate_runtime_health(self, *, mode: str, broker_health: Mapping[str, Any]) -> GovernanceReport:
        issues: List[GovernanceIssue] = []
        status = str(broker_health.get("status", "unknown")).lower()
        if mode == "live" and self.policy.require_broker_health_for_live and status != "healthy":
            issues.append(
                GovernanceIssue(
                    "live_broker_not_healthy",
                    f"Live bot requires healthy broker, got status={status}",
                    "error",
                )
            )
        return self._from_issues(issues, {"mode": mode, "broker_health": dict(broker_health)})

    def validate_signal(self, signal: Mapping[str, Any]) -> GovernanceReport:
        confidence = float(signal.get("confidence") or 0.0)
        issues: List[GovernanceIssue] = []
        if confidence < self.policy.min_signal_confidence:
            issues.append(
                GovernanceIssue(
                    "low_signal_confidence",
                    f"Signal confidence {confidence:.2f} below floor {self.policy.min_signal_confidence:.2f}",
                    "warning",
                )
            )
        return self._from_issues(issues, {"confidence": confidence})

    def validate_order_intent(self, order: Mapping[str, Any]) -> GovernanceReport:
        issues: List[GovernanceIssue] = []
        if self.policy.require_sl_tp_for_orders:
            if order.get("sl") in (None, 0, ""):
                issues.append(GovernanceIssue("missing_stop_loss", "Order intent has no stop loss", "error"))
            if order.get("tp") in (None, 0, ""):
                issues.append(GovernanceIssue("missing_take_profit", "Order intent has no take profit", "warning"))
        risk_pct = order.get("risk_pct")
        if risk_pct is not None and float(risk_pct) > 2.0:
            issues.append(GovernanceIssue("risk_pct_too_high", "Risk per trade exceeds 2%", "error"))
        return self._from_issues(issues, {"order_id": order.get("id")})

    def validate_daily_loss(self, *, balance: float, daily_pnl: float) -> GovernanceReport:
        if balance <= 0:
            return self._block("invalid_balance", "Balance must be positive")
        loss_pct = abs(min(daily_pnl, 0.0)) / balance * 100.0
        if loss_pct >= self.policy.max_daily_loss_pct:
            return GovernanceReport(
                decision=GovernanceDecision.BLOCK,
                issues=[
                    GovernanceIssue(
                        "daily_loss_limit_hit",
                        f"Daily loss {loss_pct:.2f}% reached limit {self.policy.max_daily_loss_pct:.2f}%",
                        "error",
                    )
                ],
                metadata={"loss_pct": round(loss_pct, 4)},
            )
        return GovernanceReport(GovernanceDecision.ALLOW, metadata={"loss_pct": round(loss_pct, 4)})

    def _block(self, code: str, message: str) -> GovernanceReport:
        return GovernanceReport(
            decision=GovernanceDecision.BLOCK,
            issues=[GovernanceIssue(code, message, "error")],
        )

    def _from_issues(self, issues: List[GovernanceIssue], metadata: Optional[Dict[str, Any]] = None) -> GovernanceReport:
        if any(i.severity == "error" for i in issues):
            decision = GovernanceDecision.BLOCK
        elif issues:
            decision = GovernanceDecision.REVIEW
        else:
            decision = GovernanceDecision.ALLOW
        return GovernanceReport(decision=decision, issues=issues, metadata=metadata or {})
