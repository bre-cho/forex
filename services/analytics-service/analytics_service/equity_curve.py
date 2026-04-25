"""Equity curve analysis."""
from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd


def equity_curve(pnl_series: List[float], initial_balance: float = 10_000.0) -> pd.Series:
    """Return cumulative equity curve from a list of P&L values."""
    cumulative = np.cumsum([0.0] + pnl_series)
    return pd.Series(initial_balance + cumulative, name="equity")


def peak_equity(curve: pd.Series) -> pd.Series:
    """Return running peak (high-water mark) of the equity curve."""
    return curve.cummax()


def underwater_curve(curve: pd.Series) -> pd.Series:
    """Return the drawdown in absolute terms (equity - peak)."""
    return curve - peak_equity(curve)
