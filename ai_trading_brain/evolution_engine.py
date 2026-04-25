from __future__ import annotations
import json, os, time
from typing import Any, Dict, Optional
from .memory_engine import TradeMemoryEngine


class PolicyEvolutionEngine:
    """Safe bounded evolution: mutate policy from observed outcomes, never execute directly."""

    LIMITS = {
        "min_confidence": (0.55, 0.80),
        "min_score": (0.60, 0.82),
        "max_spread_pips": (0.8, 3.5),
        "min_rr": (1.1, 2.2),
    }

    def __init__(self, memory: TradeMemoryEngine, policy_path: str = "data/trading_brain/policy.json") -> None:
        self.memory = memory
        self.policy_path = policy_path
        os.makedirs(os.path.dirname(policy_path), exist_ok=True)

    def evolve(self, current_policy: Dict[str, Any], min_samples: int = 30) -> Dict[str, Any]:
        summary = self.memory.summary(limit=200)
        if summary["count"] < min_samples:
            return {"changed": False, "reason": "not_enough_samples", "summary": summary, "policy": current_policy}

        policy = json.loads(json.dumps(current_policy))
        win_rate = summary["win_rate"]
        pf = summary["profit_factor"]
        net = summary["net_pnl"]

        if win_rate < 0.45 or pf < 1.0 or net < 0:
            policy["min_confidence"] = self._bounded(policy.get("min_confidence", 0.62) + 0.03, "min_confidence")
            policy["min_score"] = self._bounded(policy.get("min_score", 0.68) + 0.03, "min_score")
            policy["min_rr"] = self._bounded(policy.get("min_rr", 1.4) + 0.10, "min_rr")
            reason = "tighten_after_weak_performance"
        elif win_rate > 0.58 and pf > 1.25 and net > 0:
            policy["min_confidence"] = self._bounded(policy.get("min_confidence", 0.62) - 0.01, "min_confidence")
            policy["min_score"] = self._bounded(policy.get("min_score", 0.68) - 0.01, "min_score")
            reason = "slightly_expand_after_strong_performance"
        else:
            reason = "stable_no_mutation"

        payload = {"changed": policy != current_policy, "reason": reason, "summary": summary, "policy": policy, "created_at": time.time()}
        self._write_policy(payload)
        return payload

    def _bounded(self, value: float, key: str) -> float:
        lo, hi = self.LIMITS[key]
        return round(max(lo, min(hi, float(value))), 4)

    def _write_policy(self, payload: Dict[str, Any]) -> None:
        with open(self.policy_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
