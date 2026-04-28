from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from .brain_contracts import BrainAction, BrainCycleResult, BrainInput, BrainStage, ExecutionIntent, StageDecision
from .decision_engine import DecisionInput, ForexDecisionEngine
from .governance import TradingBrainGovernance
from .memory_engine import TradeMemoryEngine
from .evolution_engine import PolicyEvolutionEngine
from .engine_registry import TradingEngineRegistry


class UnifiedTradePipeline:
    """
    Closed-loop brain pipeline.

    Contract:
    MARKET_INGEST → CONTEXT_BUILD → SIGNAL_SCAN → STRATEGY_CAUSAL → UTILITY_GAME_THEORY
    → POLICY_PREFLIGHT → RISK_CAPITAL → EXECUTION_PLAN → BROKER_ROUTE
    → POSITION_MONITOR → INCIDENT_RECOVERY → MEMORY_LEARNING

    The pipeline returns an execution intent only after every upstream gate agrees.
    It does not place orders by itself.
    """

    def __init__(
        self,
        *,
        decision_engine: Optional[ForexDecisionEngine] = None,
        governance: Optional[TradingBrainGovernance] = None,
        memory: Optional[TradeMemoryEngine] = None,
        evolution: Optional[PolicyEvolutionEngine] = None,
        registry: Optional[TradingEngineRegistry] = None,
    ) -> None:
        self.decision_engine = decision_engine or ForexDecisionEngine()
        self.governance = governance or TradingBrainGovernance()
        self.memory = memory or TradeMemoryEngine()
        self.evolution = evolution or PolicyEvolutionEngine(self.memory)
        self.registry = registry or TradingEngineRegistry()

    def run_cycle(self, item: BrainInput) -> BrainCycleResult:
        cycle_id = f"brain-{int(time.time())}-{uuid.uuid4().hex[:8]}"
        decisions: List[StageDecision] = []

        def add(stage: BrainStage, action: BrainAction, reason: str, score: float = 0.0, payload: Optional[Dict[str, Any]] = None) -> StageDecision:
            now = time.time()
            row = StageDecision(stage=stage, action=action, reason=reason, score=round(float(score), 4), payload=payload or {}, started_at=now, finished_at=time.time())
            decisions.append(row)
            return row

        market_ok, market_reason = self._validate_market(item)
        add(BrainStage.MARKET_INGEST, BrainAction.ALLOW if market_ok else BrainAction.BLOCK, market_reason)
        if not market_ok:
            return self._finish(cycle_id, BrainAction.BLOCK, market_reason, 0.0, None, None, decisions)

        context = self._build_context(item)
        add(BrainStage.CONTEXT_BUILD, BrainAction.ALLOW, "context_ready", payload=context)

        selected = self._select_signal(item.signals)
        if not selected:
            add(BrainStage.SIGNAL_SCAN, BrainAction.SKIP, "no_qualified_signal")
            return self._finish(cycle_id, BrainAction.SKIP, "no_qualified_signal", 0.0, None, None, decisions)
        add(BrainStage.SIGNAL_SCAN, BrainAction.ALLOW, "signal_selected", float(selected.get("confidence", 0.0)), selected)

        causal_score = self._safe_score_engine("causal_strategy_engine", selected, context, default=0.5)
        add(BrainStage.STRATEGY_CAUSAL, BrainAction.ALLOW, "causal_score_ready", causal_score)

        utility_score = self._safe_score_engine("utility_optimization_engine", selected, context, default=0.5)
        game_score = self._safe_score_engine("game_theory_engine", selected, context, default=0.5)
        ensemble_context = {**context, "causal_score": causal_score, "utility_score": utility_score, "game_score": game_score}
        add(BrainStage.UTILITY_GAME_THEORY, BrainAction.ALLOW, "utility_game_theory_ready", (utility_score + game_score) / 2.0)

        ok, reason = self.governance.preflight(ensemble_context)
        add(BrainStage.POLICY_PREFLIGHT, BrainAction.ALLOW if ok else BrainAction.BLOCK, reason)
        if not ok:
            return self._finish(cycle_id, BrainAction.BLOCK, reason, 0.0, selected, None, decisions)

        decision_input = self._to_decision_input(selected, ensemble_context)
        decision = self.decision_engine.decide(decision_input)
        final_score = self._blend_score(decision.score, causal_score, utility_score, game_score)
        brain_action = BrainAction(decision.action) if decision.action in BrainAction._value2member_map_ else BrainAction.SKIP
        add(BrainStage.RISK_CAPITAL, brain_action, decision.reason, final_score, {"decision": decision.__dict__})

        if brain_action not in {BrainAction.ALLOW, BrainAction.REDUCE}:
            return self._finish(cycle_id, brain_action, decision.reason, final_score, selected, None, decisions, decision.policy_snapshot)

        intent = ExecutionIntent(
            symbol=decision_input.symbol,
            side=decision_input.direction,
            lot_multiplier=decision.lot_multiplier,
            risk_pct=float(item.settings.get("risk_pct", 0.5)) * float(decision.lot_multiplier),
            sl_pips=decision.suggested_sl_pips,
            tp_pips=decision.suggested_tp_pips,
            broker=item.broker,
            metadata={"cycle_id": cycle_id, "decision_reason": decision.reason, "score": final_score},
        )
        add(BrainStage.EXECUTION_PLAN, brain_action, "execution_intent_ready", final_score, intent.__dict__)

        broker_ok, broker_reason = self._validate_broker_route(item.broker)
        add(BrainStage.BROKER_ROUTE, BrainAction.ALLOW if broker_ok else BrainAction.BLOCK, broker_reason)
        if not broker_ok:
            return self._finish(cycle_id, BrainAction.BLOCK, broker_reason, final_score, selected, None, decisions, decision.policy_snapshot)

        add(BrainStage.POSITION_MONITOR, BrainAction.ALLOW, "position_monitor_attached", payload={"open_positions": len(item.positions)})
        add(BrainStage.INCIDENT_RECOVERY, BrainAction.ALLOW, "no_incident_detected")
        add(BrainStage.MEMORY_LEARNING, BrainAction.ALLOW, "cycle_ready_for_outcome_feedback")
        return self._finish(cycle_id, brain_action, decision.reason, final_score, selected, intent, decisions, decision.policy_snapshot)

    def _validate_market(self, item: BrainInput) -> Tuple[bool, str]:
        if not item.symbol:
            return False, "missing_symbol"
        if item.market.get("market_data_ok") is False:
            return False, "market_data_invalid"
        if item.market.get("stale") is True:
            return False, "market_data_stale"
        return True, "market_data_ready"

    def _build_context(self, item: BrainInput) -> Dict[str, Any]:
        return {
            "symbol": item.symbol,
            "timeframe": item.timeframe,
            "broker": item.broker,
            "broker_connected": bool(item.market.get("broker_connected", item.broker != "stub")),
            "market_data_ok": item.market.get("market_data_ok", True),
            "spread_pips": float(item.market.get("spread_pips", 0.0)),
            "atr_pips": float(item.market.get("atr_pips", 0.0)),
            "daily_loss_pct": float(item.account.get("daily_loss_pct", 0.0)),
            "consecutive_losses": int(item.account.get("consecutive_losses", 0)),
            "account_equity": float(item.account.get("equity", 0.0)),
            "open_positions": len(item.positions),
            "session_score": float(item.market.get("session_score", 0.5)),
            "volatility_score": float(item.market.get("volatility_score", 0.5)),
            **item.telemetry,
        }

    def _select_signal(self, signals: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        valid = [s for s in signals if str(s.get("direction") or s.get("side") or "").upper() in {"BUY", "SELL"}]
        if not valid:
            return None
        return sorted(valid, key=lambda s: (float(s.get("priority", 0.0)), float(s.get("confidence", 0.0))), reverse=True)[0]

    def _safe_score_engine(self, name: str, signal: Dict[str, Any], context: Dict[str, Any], *, default: float) -> float:
        engine = self.registry.get(name)
        if engine is None:
            return default
        try:
            for method in ("score", "predict", "evaluate"):
                fn = getattr(engine, method, None)
                if callable(fn):
                    value = fn(signal=signal, context=context)
                    if isinstance(value, dict):
                        value = value.get("score", value.get("confidence", default))
                    return max(0.0, min(1.0, float(value)))
        except Exception:  # noqa: BLE001
            return default
        return default

    def _to_decision_input(self, signal: Dict[str, Any], context: Dict[str, Any]) -> DecisionInput:
        return DecisionInput(
            symbol=str(signal.get("symbol") or context.get("symbol")),
            direction=str(signal.get("direction") or signal.get("side")).upper(),
            confidence=float(signal.get("confidence", 0.0)),
            spread_pips=float(signal.get("spread_pips", context.get("spread_pips", 0.0))),
            atr_pips=float(signal.get("atr_pips", context.get("atr_pips", 0.0))),
            rr=float(signal.get("rr", context.get("rr", 0.0))),
            trend_strength=float(signal.get("trend_strength", context.get("trend_strength", 0.0))),
            session_score=float(context.get("session_score", 0.5)),
            volatility_score=float(context.get("volatility_score", 0.5)),
            account_equity=float(context.get("account_equity", 0.0)),
            open_positions=int(context.get("open_positions", 0)),
            metadata={"signal": signal, "context": context},
        )

    def _blend_score(self, decision_score: float, causal: float, utility: float, game: float) -> float:
        return round((decision_score * 0.58) + (causal * 0.16) + (utility * 0.16) + (game * 0.10), 4)

    def _validate_broker_route(self, broker: str) -> Tuple[bool, str]:
        health = self.registry.health()
        if broker == "stub":
            return True, "stub_broker_route"
        failed = health.get("critical_failed", [])
        if failed:
            return False, "critical_engine_failed:" + ",".join(failed)
        return True, "broker_route_ready"

    def _finish(self, cycle_id: str, action: BrainAction, reason: str, score: float,
                selected: Optional[Dict[str, Any]], intent: Optional[ExecutionIntent],
                decisions: List[StageDecision], policy: Optional[Dict[str, Any]] = None) -> BrainCycleResult:
        return BrainCycleResult(
            cycle_id=cycle_id,
            action=action,
            reason=reason,
            final_score=round(float(score), 4),
            selected_signal=selected,
            execution_intent=intent,
            stage_decisions=decisions,
            policy_snapshot=policy or self.decision_engine.policy.copy(),
        )
