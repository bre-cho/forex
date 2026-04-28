from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class BrainStage(str, Enum):
    MARKET_INGEST = "MARKET_INGEST"
    CONTEXT_BUILD = "CONTEXT_BUILD"
    SIGNAL_SCAN = "SIGNAL_SCAN"
    STRATEGY_CAUSAL = "STRATEGY_CAUSAL"
    UTILITY_GAME_THEORY = "UTILITY_GAME_THEORY"
    POLICY_PREFLIGHT = "POLICY_PREFLIGHT"
    RISK_CAPITAL = "RISK_CAPITAL"
    EXECUTION_PLAN = "EXECUTION_PLAN"
    BROKER_ROUTE = "BROKER_ROUTE"
    POSITION_MONITOR = "POSITION_MONITOR"
    INCIDENT_RECOVERY = "INCIDENT_RECOVERY"
    MEMORY_LEARNING = "MEMORY_LEARNING"


class BrainAction(str, Enum):
    ALLOW = "ALLOW"
    REDUCE = "REDUCE"
    SKIP = "SKIP"
    BLOCK = "BLOCK"
    PAUSE = "PAUSE"
    RECOVER = "RECOVER"


@dataclass
class StageDecision:
    stage: BrainStage
    action: BrainAction
    reason: str
    score: float = 0.0
    payload: Dict[str, Any] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)
    finished_at: float = field(default_factory=time.time)

    @property
    def latency_ms(self) -> float:
        return round((self.finished_at - self.started_at) * 1000.0, 3)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["stage"] = self.stage.value
        data["action"] = self.action.value
        data["latency_ms"] = self.latency_ms
        return data


@dataclass
class BrainInput:
    symbol: str
    timeframe: str = "M5"
    broker: str = "stub"
    market: Dict[str, Any] = field(default_factory=dict)
    account: Dict[str, Any] = field(default_factory=dict)
    positions: List[Dict[str, Any]] = field(default_factory=list)
    signals: List[Dict[str, Any]] = field(default_factory=list)
    settings: Dict[str, Any] = field(default_factory=dict)
    telemetry: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionIntent:
    symbol: str
    side: str
    order_type: str = "MARKET"
    lot_multiplier: float = 1.0
    risk_pct: float = 0.0
    sl_pips: Optional[float] = None
    tp_pips: Optional[float] = None
    broker: str = "stub"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BrainCycleResult:
    cycle_id: str
    action: BrainAction
    reason: str
    final_score: float
    selected_signal: Optional[Dict[str, Any]] = None
    execution_intent: Optional[ExecutionIntent] = None
    stage_decisions: List[StageDecision] = field(default_factory=list)
    policy_snapshot: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "action": self.action.value,
            "reason": self.reason,
            "final_score": self.final_score,
            "selected_signal": self.selected_signal,
            "execution_intent": asdict(self.execution_intent) if self.execution_intent else None,
            "stage_decisions": [s.to_dict() for s in self.stage_decisions],
            "policy_snapshot": self.policy_snapshot,
            "created_at": self.created_at,
        }
