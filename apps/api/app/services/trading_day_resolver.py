"""TradingDayResolver — resolve the current trading day per broker/account config.

P0.6 fix: daily TP/loss state uses date.today() which can roll at midnight UTC
but broker session might roll at a different time (e.g. 17:00 NY / 00:00 UTC+3).

Usage:
    resolver = TradingDayResolver(rollover_hour_utc=22)  # 22:00 UTC = NY close
    trading_day = resolver.resolve(now_utc=datetime.now(timezone.utc))
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional


class TradingDayResolver:
    """Resolve the current Forex/CFD trading day based on rollover configuration.

    Parameters
    ----------
    rollover_hour_utc:
        The UTC hour at which the broker rolls over to the next trading day.
        Default 22 (22:00 UTC = typical New York close / FX rollover).
        Set to 0 for midnight UTC (default server behavior).
    rollover_minute_utc:
        The UTC minute of rollover.  Defaults to 0.

    Examples
    --------
    NY rollover 22:00 UTC:
        - At 23:00 UTC Tuesday -> returns Wednesday
        - At 21:59 UTC Tuesday -> returns Tuesday

    Midnight UTC:
        - At 00:01 UTC -> returns today
    """

    def __init__(
        self,
        rollover_hour_utc: int = 22,
        rollover_minute_utc: int = 0,
    ) -> None:
        self._rollover_hour = int(rollover_hour_utc)
        self._rollover_minute = int(rollover_minute_utc)

    def resolve(self, now_utc: Optional[datetime] = None) -> date:
        """Return the trading day for the given UTC timestamp.

        Args:
            now_utc: UTC datetime to resolve. Uses current UTC time if None.

        Returns:
            The trading day as a date object.
        """
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)
        now = now_utc.astimezone(timezone.utc)
        rollover_today = now.replace(
            hour=self._rollover_hour,
            minute=self._rollover_minute,
            second=0,
            microsecond=0,
        )
        if now >= rollover_today:
            # Past rollover: treat as the NEXT calendar day's trading session
            return (now.date() + timedelta(days=1))
        return now.date()

    @classmethod
    def default(cls) -> "TradingDayResolver":
        """Return a resolver configured for the default Forex rollover (22:00 UTC)."""
        return cls(rollover_hour_utc=22, rollover_minute_utc=0)

    @classmethod
    def midnight_utc(cls) -> "TradingDayResolver":
        """Return a resolver for midnight UTC rollover (matches date.today() at UTC)."""
        return cls(rollover_hour_utc=0, rollover_minute_utc=0)
