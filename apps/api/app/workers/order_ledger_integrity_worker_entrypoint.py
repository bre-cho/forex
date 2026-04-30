"""Nightly order-ledger integrity worker entrypoint.

Run as: python -m app.workers.order_ledger_integrity_worker_entrypoint

This worker executes the order-ledger integrity checker on a schedule in
production, independent from API and reconciliation workers.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, timedelta, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("order_ledger_integrity_worker")


def _seconds_until_next_run(*, now: datetime, run_hour_utc: int, run_minute_utc: int) -> float:
    candidate = now.replace(hour=run_hour_utc, minute=run_minute_utc, second=0, microsecond=0)
    if candidate <= now:
        candidate = candidate + timedelta(days=1)
    return max(1.0, float((candidate - now).total_seconds()))


async def _run_once() -> dict:
    from app.workers.verify_order_ledger_integrity import run_once

    return await run_once()


async def _main() -> None:
    from app.core.db import init_db  # noqa: F401

    stop_event = asyncio.Event()

    def _handle_signal(sig: int, _frame: object) -> None:
        logger.info("order_ledger_integrity_worker: received signal %s, shutting down", sig)
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    run_hour_utc = int(os.getenv("INTEGRITY_RUN_HOUR_UTC", "0"))
    run_minute_utc = int(os.getenv("INTEGRITY_RUN_MINUTE_UTC", "15"))
    run_on_startup = str(os.getenv("INTEGRITY_RUN_ON_STARTUP", "true")).lower() in {"1", "true", "yes"}

    logger.info(
        "order_ledger_integrity_worker: starting schedule at %02d:%02d UTC (run_on_startup=%s)",
        run_hour_utc,
        run_minute_utc,
        run_on_startup,
    )

    if run_on_startup:
        report = await _run_once()
        logger.info(
            "order_ledger_integrity_worker: startup run ok=%s critical=%s warning=%s",
            report.get("ok"),
            report.get("critical_count"),
            report.get("warning_count"),
        )

    while not stop_event.is_set():
        now = datetime.now(timezone.utc)
        wait_seconds = _seconds_until_next_run(
            now=now,
            run_hour_utc=run_hour_utc,
            run_minute_utc=run_minute_utc,
        )
        logger.info("order_ledger_integrity_worker: next run in %.1fs", wait_seconds)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=wait_seconds)
            break
        except asyncio.TimeoutError:
            pass

        if stop_event.is_set():
            break

        report = await _run_once()
        logger.info(
            "order_ledger_integrity_worker: nightly run ok=%s critical=%s warning=%s",
            report.get("ok"),
            report.get("critical_count"),
            report.get("warning_count"),
        )

    logger.info("order_ledger_integrity_worker: stopped cleanly")


if __name__ == "__main__":
    asyncio.run(_main())
