"""Session-level win-rate breakdown.

Classifies each trade by its forex trading session (Sydney, Tokyo, London,
New York) based on UTC open time and computes win-rate and P&L per session.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List


# Session windows in UTC hours (half-open intervals [start, end)).
_SESSION_WINDOWS: Dict[str, tuple[int, int]] = {
    "sydney":   (21, 6),   # 21:00–06:00 UTC (wraps midnight)
    "tokyo":    (0,  9),   # 00:00–09:00 UTC
    "london":   (7,  16),  # 07:00–16:00 UTC
    "new_york": (12, 21),  # 12:00–21:00 UTC
}


def _classify_session(utc_hour: int) -> str:
    """Return the primary session for a given UTC hour.

    When multiple sessions overlap, the later-opening session takes
    priority (New York > London > Tokyo > Sydney).
    """
    for session in ("new_york", "london", "tokyo", "sydney"):
        start, end = _SESSION_WINDOWS[session]
        if start < end:
            if start <= utc_hour < end:
                return session
        else:
            # Wraps midnight.
            if utc_hour >= start or utc_hour < end:
                return session
    return "unknown"


@dataclass
class SessionStats:
    session: str
    trade_count: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    win_rate: float = 0.0
    avg_pnl: float = 0.0


def compute_session_win_rates(
    trades: List[Dict],
) -> Dict[str, SessionStats]:
    """Return per-session win-rate stats from a list of trade dicts.

    Each trade dict must have:
        ``pnl``        (float) — realised P&L
        ``open_time``  (float | int) — UTC Unix timestamp of trade open

    Missing ``open_time`` trades are counted under ``"unknown"``.
    """
    stats: Dict[str, SessionStats] = {}

    for trade in trades:
        pnl = float(trade.get("pnl") or trade.get("profit") or 0.0)
        ts = trade.get("open_time") or trade.get("time") or 0.0
        try:
            utc_hour = int(math.floor(float(ts) / 3600) % 24)
        except (TypeError, ValueError):
            utc_hour = -1

        session = _classify_session(utc_hour) if utc_hour >= 0 else "unknown"

        if session not in stats:
            stats[session] = SessionStats(session=session)
        s = stats[session]
        s.trade_count += 1
        s.total_pnl += pnl
        if pnl > 0:
            s.wins += 1
        else:
            s.losses += 1

    for s in stats.values():
        s.win_rate = s.wins / s.trade_count if s.trade_count > 0 else 0.0
        s.avg_pnl = s.total_pnl / s.trade_count if s.trade_count > 0 else 0.0

    return stats
