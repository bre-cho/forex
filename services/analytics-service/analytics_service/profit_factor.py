"""Profit factor calculation."""
from __future__ import annotations

from typing import List


def compute_profit_factor(pnl_series: List[float]) -> float:
    """Profit factor = gross profit / abs(gross loss). Returns inf if no losses."""
    gross_profit = sum(p for p in pnl_series if p > 0)
    gross_loss = abs(sum(p for p in pnl_series if p < 0))
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss
