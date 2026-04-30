from __future__ import annotations

from datetime import datetime, timezone

from app.workers.order_ledger_integrity_worker_entrypoint import _seconds_until_next_run


def test_seconds_until_next_run_same_day() -> None:
    now = datetime(2026, 4, 30, 0, 0, 0, tzinfo=timezone.utc)
    secs = _seconds_until_next_run(now=now, run_hour_utc=0, run_minute_utc=15)
    assert int(secs) == 900


def test_seconds_until_next_run_next_day_when_past_schedule() -> None:
    now = datetime(2026, 4, 30, 23, 59, 0, tzinfo=timezone.utc)
    secs = _seconds_until_next_run(now=now, run_hour_utc=23, run_minute_utc=0)
    assert int(secs) == 23 * 3600 + 1 * 60
