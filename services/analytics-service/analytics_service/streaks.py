"""Streak and consecutive-loss/win statistics."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass
class StreakStats:
    max_consecutive_losses: int
    max_consecutive_wins: int
    current_streak: int          # positive = wins, negative = losses
    avg_loss_streak: float
    avg_win_streak: float


def compute_streaks(pnl_series: List[float]) -> StreakStats:
    """Compute win/loss streak statistics from a P&L series.

    A *win* is any trade with P&L > 0; a *loss* is P&L ≤ 0.
    """
    if not pnl_series:
        return StreakStats(0, 0, 0, 0.0, 0.0)

    max_losses = 0
    max_wins = 0
    current = 0  # positive = consecutive wins, negative = consecutive losses
    loss_streaks: List[int] = []
    win_streaks: List[int] = []

    for pnl in pnl_series:
        if pnl > 0:
            if current < 0:
                loss_streaks.append(abs(current))
                current = 0
            current += 1
            max_wins = max(max_wins, current)
        else:
            if current > 0:
                win_streaks.append(current)
                current = 0
            current -= 1
            max_losses = max(max_losses, abs(current))

    # Flush last open streak.
    if current > 0:
        win_streaks.append(current)
    elif current < 0:
        loss_streaks.append(abs(current))

    avg_loss = sum(loss_streaks) / len(loss_streaks) if loss_streaks else 0.0
    avg_win = sum(win_streaks) / len(win_streaks) if win_streaks else 0.0

    return StreakStats(
        max_consecutive_losses=max_losses,
        max_consecutive_wins=max_wins,
        current_streak=current,
        avg_loss_streak=avg_loss,
        avg_win_streak=avg_win,
    )
