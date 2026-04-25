"""Drawdown analysis."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

import numpy as np
import pandas as pd

from .equity_curve import equity_curve, peak_equity


@dataclass
class DrawdownStats:
    max_drawdown_pct: float
    max_drawdown_abs: float
    avg_drawdown_pct: float
    recovery_periods: int


def compute_drawdown(pnl_series: List[float], initial_balance: float = 10_000.0) -> DrawdownStats:
    curve = equity_curve(pnl_series, initial_balance)
    peak = peak_equity(curve)
    drawdown_abs = curve - peak
    drawdown_pct = drawdown_abs / peak * 100

    max_dd_abs = float(drawdown_abs.min())
    max_dd_pct = float(drawdown_pct.min())
    avg_dd_pct = float(drawdown_pct[drawdown_pct < 0].mean()) if (drawdown_pct < 0).any() else 0.0
    recoveries = int((np.diff((drawdown_abs < 0).astype(int)) == -1).sum())

    return DrawdownStats(
        max_drawdown_pct=max_dd_pct,
        max_drawdown_abs=max_dd_abs,
        avg_drawdown_pct=avg_dd_pct,
        recovery_periods=recoveries,
    )
