"""analytics_service — trading performance analytics."""

__version__ = "0.2.0"

from .calmar import calmar_ratio
from .drawdown import DrawdownStats, compute_drawdown
from .equity_curve import equity_curve, peak_equity, underwater_curve
from .expectancy import ExpectancyStats, compute_expectancy
from .profit_factor import compute_profit_factor
from .session_analysis import SessionStats, compute_session_win_rates
from .sharpe import sharpe_ratio, sortino_ratio
from .streaks import StreakStats, compute_streaks

__all__ = [
    "calmar_ratio",
    "compute_drawdown",
    "DrawdownStats",
    "equity_curve",
    "peak_equity",
    "underwater_curve",
    "compute_expectancy",
    "ExpectancyStats",
    "compute_profit_factor",
    "compute_session_win_rates",
    "SessionStats",
    "sharpe_ratio",
    "sortino_ratio",
    "compute_streaks",
    "StreakStats",
]

