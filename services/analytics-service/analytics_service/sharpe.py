"""Sharpe and Sortino ratio calculations."""
from __future__ import annotations

from typing import List

import numpy as np


def sharpe_ratio(pnl_series: List[float], risk_free_rate: float = 0.0) -> float:
    """Annualised Sharpe ratio (assumes daily returns)."""
    if len(pnl_series) < 2:
        return 0.0
    arr = np.array(pnl_series, dtype=float)
    excess = arr - risk_free_rate / 252
    std = excess.std(ddof=1)
    if std == 0:
        return 0.0
    return float((excess.mean() / std) * np.sqrt(252))


def sortino_ratio(pnl_series: List[float], risk_free_rate: float = 0.0) -> float:
    """Annualised Sortino ratio (penalises only downside volatility)."""
    if len(pnl_series) < 2:
        return 0.0
    arr = np.array(pnl_series, dtype=float)
    excess = arr - risk_free_rate / 252
    downside = excess[excess < 0]
    downside_std = downside.std(ddof=1) if len(downside) > 1 else 0.0
    if downside_std == 0:
        return 0.0
    return float((excess.mean() / downside_std) * np.sqrt(252))
