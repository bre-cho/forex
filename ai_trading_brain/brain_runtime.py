from __future__ import annotations
from typing import Any, Dict
from .decision_engine import DecisionInput, ForexDecisionEngine, DecisionResult
from .governance import TradingBrainGovernance
from .memory_engine import TradeMemoryEngine, TradeOutcome
from .evolution_engine import PolicyEvolutionEngine


class ForexBrainRuntime:
    """Single facade used by BotRuntime: governance -> decision -> memory -> evolution."""

    def __init__(self, policy: Dict[str, Any] | None = None, governance_config: Dict[str, Any] | None = None) -> None:
        self.decision = ForexDecisionEngine(policy)
        self.governance = TradingBrainGovernance(governance_config)
        self.memory = TradeMemoryEngine()
        self.evolution = PolicyEvolutionEngine(self.memory)

    def decide(self, signal: Dict[str, Any], context: Dict[str, Any]) -> DecisionResult:
        ok, reason = self.governance.preflight(context)
        if not ok:
            return DecisionResult(action="BLOCK", reason=reason, score=0.0, policy_snapshot=self.decision.policy.copy())
        item = DecisionInput(
            symbol=str(signal.get("symbol") or context.get("symbol") or "EURUSD"),
            direction=str(signal.get("direction") or signal.get("side") or "HOLD").upper(),
            confidence=float(signal.get("confidence", 0.0)),
            spread_pips=float(signal.get("spread_pips", context.get("spread_pips", 0.0))),
            atr_pips=float(signal.get("atr_pips", context.get("atr_pips", 0.0))),
            rr=float(signal.get("rr", context.get("rr", 0.0))),
            trend_strength=float(signal.get("trend_strength", context.get("trend_strength", 0.0))),
            session_score=float(signal.get("session_score", context.get("session_score", 0.5))),
            volatility_score=float(signal.get("volatility_score", context.get("volatility_score", 0.5))),
            account_equity=float(context.get("account_equity", 0.0)),
            open_positions=int(context.get("open_positions", 0)),
            metadata={"raw_signal": signal, "context": context},
        )
        return self.decision.decide(item)

    def record_outcome(self, outcome: TradeOutcome) -> None:
        self.memory.record(outcome)

    def evolve_policy(self) -> Dict[str, Any]:
        payload = self.evolution.evolve(self.decision.policy)
        if payload.get("changed"):
            self.decision = ForexDecisionEngine(payload["policy"])
        return payload
