from __future__ import annotations
import json, os, time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TradeOutcome:
    trade_id: str
    symbol: str
    direction: str
    opened_at: float
    closed_at: float
    entry_price: float
    exit_price: float
    pnl: float
    pnl_pips: float
    decision_score: float
    decision_reason: str
    policy_snapshot: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


class TradeMemoryEngine:
    """Append-only JSONL memory for trade outcomes and policy feedback."""

    def __init__(self, path: str = "data/trading_brain/trade_memory.jsonl") -> None:
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)

    def record(self, outcome: TradeOutcome) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(outcome), ensure_ascii=False) + "\n")

    def recent(self, limit: int = 200) -> List[TradeOutcome]:
        if not os.path.exists(self.path):
            return []
        with open(self.path, "r", encoding="utf-8") as f:
            lines = f.readlines()[-limit:]
        items: List[TradeOutcome] = []
        for line in lines:
            try:
                items.append(TradeOutcome(**json.loads(line)))
            except Exception:
                continue
        return items

    def summary(self, limit: int = 200) -> Dict[str, Any]:
        trades = self.recent(limit)
        if not trades:
            return {"count": 0, "win_rate": 0.0, "net_pnl": 0.0, "avg_score": 0.0, "profit_factor": 0.0}
        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl < 0]
        gross_win = sum(t.pnl for t in wins)
        gross_loss = abs(sum(t.pnl for t in losses))
        return {
            "count": len(trades),
            "win_rate": round(len(wins) / len(trades), 4),
            "net_pnl": round(sum(t.pnl for t in trades), 4),
            "avg_score": round(sum(t.decision_score for t in trades) / len(trades), 4),
            "profit_factor": round(gross_win / gross_loss, 4) if gross_loss else 999.0,
            "last_updated_at": time.time(),
        }
