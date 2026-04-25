from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class DecisionInput:
    symbol: str
    direction: str  # BUY | SELL | HOLD
    confidence: float
    spread_pips: float = 0.0
    atr_pips: float = 0.0
    rr: float = 0.0
    trend_strength: float = 0.0
    session_score: float = 0.5
    volatility_score: float = 0.5
    account_equity: float = 0.0
    open_positions: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DecisionResult:
    action: str  # ALLOW | SKIP | BLOCK | REDUCE
    reason: str
    score: float
    lot_multiplier: float = 1.0
    suggested_sl_pips: Optional[float] = None
    suggested_tp_pips: Optional[float] = None
    policy_snapshot: Dict[str, Any] = field(default_factory=dict)


class ForexDecisionEngine:
    """Forex-specific decision scorer. Does not execute trades."""

    DEFAULT_POLICY = {
        "min_confidence": 0.62,
        "min_score": 0.68,
        "max_spread_pips": 2.5,
        "min_rr": 1.4,
        "max_open_positions": 3,
        "base_sl_atr_mult": 1.25,
        "base_tp_rr": 1.8,
        "weights": {
            "confidence": 0.32,
            "rr": 0.18,
            "trend_strength": 0.18,
            "spread_quality": 0.14,
            "session_score": 0.10,
            "volatility_score": 0.08,
        },
    }

    def __init__(self, policy: Optional[Dict[str, Any]] = None) -> None:
        self.policy = self._merge_policy(self.DEFAULT_POLICY, policy or {})

    def decide(self, item: DecisionInput) -> DecisionResult:
        p = self.policy
        if item.direction not in {"BUY", "SELL"}:
            return self._result("SKIP", "no_trade_direction", 0.0)
        if item.open_positions >= int(p["max_open_positions"]):
            return self._result("BLOCK", "max_open_positions_reached", 0.0)
        if item.confidence < float(p["min_confidence"]):
            return self._result("SKIP", "confidence_below_policy", item.confidence)
        if item.spread_pips > float(p["max_spread_pips"]):
            return self._result("BLOCK", "spread_too_high", 0.0)
        if item.rr and item.rr < float(p["min_rr"]):
            return self._result("SKIP", "rr_below_policy", item.rr)

        score = self._score(item)
        if score < float(p["min_score"]):
            return self._result("SKIP", "composite_score_below_policy", score)

        lot_multiplier = self._lot_multiplier(score)
        sl = max(5.0, item.atr_pips * float(p["base_sl_atr_mult"])) if item.atr_pips else None
        tp = sl * float(p["base_tp_rr"]) if sl else None
        action = "ALLOW" if score >= 0.76 else "REDUCE"
        reason = "high_quality_trade" if action == "ALLOW" else "valid_but_reduced_risk"
        return self._result(action, reason, score, lot_multiplier, sl, tp)

    def _score(self, item: DecisionInput) -> float:
        w = self.policy["weights"]
        spread_quality = max(0.0, 1.0 - (item.spread_pips / max(float(self.policy["max_spread_pips"]), 0.01)))
        rr_score = min(1.0, item.rr / 3.0) if item.rr else 0.5
        components = {
            "confidence": self._clamp(item.confidence),
            "rr": self._clamp(rr_score),
            "trend_strength": self._clamp(item.trend_strength),
            "spread_quality": self._clamp(spread_quality),
            "session_score": self._clamp(item.session_score),
            "volatility_score": self._clamp(item.volatility_score),
        }
        return round(sum(components[k] * float(w[k]) for k in components), 4)

    def _lot_multiplier(self, score: float) -> float:
        if score >= 0.90:
            return 1.25
        if score >= 0.82:
            return 1.0
        if score >= 0.76:
            return 0.75
        return 0.5

    def _result(self, action: str, reason: str, score: float, lot_multiplier: float = 1.0,
                sl: Optional[float] = None, tp: Optional[float] = None) -> DecisionResult:
        return DecisionResult(action, reason, round(float(score), 4), lot_multiplier, sl, tp, self.policy.copy())

    @staticmethod
    def _clamp(v: float) -> float:
        return max(0.0, min(1.0, float(v)))

    @classmethod
    def _merge_policy(cls, base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        merged = {**base, **override}
        merged["weights"] = {**base.get("weights", {}), **override.get("weights", {})}
        return merged
