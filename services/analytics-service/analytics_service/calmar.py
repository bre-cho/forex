"""Calmar ratio and related return/drawdown metrics."""
from __future__ import annotations

from typing import List

from .drawdown import compute_drawdown


def calmar_ratio(pnl_series: List[float], initial_balance: float = 10_000.0) -> float:
    """Annualised Calmar ratio = annualised return / abs(max drawdown pct).

    Assumes each entry in *pnl_series* represents one daily P&L value.
    Returns 0.0 when there is no drawdown (infinite Calmar is treated as
    undefined/0.0 to avoid downstream division errors).
    """
    if len(pnl_series) < 2:
        return 0.0

    total_return = sum(pnl_series)
    n_days = len(pnl_series)
    annualised_return = (total_return / initial_balance) * (252 / n_days) * 100.0

    stats = compute_drawdown(pnl_series, initial_balance)
    max_dd_pct = abs(stats.max_drawdown_pct)
    if max_dd_pct == 0.0:
        return 0.0

    return annualised_return / max_dd_pct
