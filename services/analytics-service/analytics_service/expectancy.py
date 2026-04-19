"""Trade expectancy calculation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass
class ExpectancyStats:
    win_rate: float
    avg_win: float
    avg_loss: float
    expectancy: float
    trade_count: int


def compute_expectancy(pnl_series: List[float]) -> ExpectancyStats:
    """Calculate trade expectancy: E = (win_rate * avg_win) - (loss_rate * abs(avg_loss))."""
    if not pnl_series:
        return ExpectancyStats(0, 0, 0, 0, 0)

    wins = [p for p in pnl_series if p > 0]
    losses = [p for p in pnl_series if p <= 0]
    total = len(pnl_series)

    win_rate = len(wins) / total
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    loss_rate = 1.0 - win_rate
    expectancy = (win_rate * avg_win) + (loss_rate * avg_loss)

    return ExpectancyStats(
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        expectancy=expectancy,
        trade_count=total,
    )
