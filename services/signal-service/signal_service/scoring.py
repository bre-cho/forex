"""Signal scoring — ranks signals by quality."""
from __future__ import annotations

from .signal_builder import TradingSignal


def score_signal(signal: TradingSignal) -> float:
    """
    Score a signal 0–100 based on confidence and R:R ratio.
    Higher is better.
    """
    score = signal.confidence * 60  # up to 60 points from confidence

    if signal.entry_price and signal.stop_loss and signal.take_profit:
        risk = abs(signal.entry_price - signal.stop_loss)
        reward = abs(signal.take_profit - signal.entry_price)
        if risk > 0:
            rr = reward / risk
            # Add up to 40 points for R:R ratio (capped at 4:1)
            score += min(rr / 4.0, 1.0) * 40

    return round(min(score, 100.0), 2)


def filter_signals(signals: list, min_score: float = 50.0) -> list:
    """Filter signals below the minimum score threshold."""
    return [s for s in signals if score_signal(s) >= min_score]
