"""Submit outbox recovery worker standalone entrypoint.

Run as: python -m app.workers.submit_outbox_recovery_worker_entrypoint
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("submit_outbox_recovery_worker")


async def _main() -> None:
    from app.core.db import init_db  # noqa: F401

    stop_event = asyncio.Event()

    def _handle_signal(sig: int, _frame: object) -> None:
        logger.info("submit_outbox_recovery_worker: received signal %s, shutting down", sig)
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    poll_interval = float(os.getenv("SUBMIT_OUTBOX_RECOVERY_POLL_INTERVAL_SECONDS", "10"))

    from app.workers.submit_outbox_recovery_worker import run_submit_outbox_recovery_worker

    await run_submit_outbox_recovery_worker(
        poll_interval=poll_interval,
        stop_event=stop_event,
    )


if __name__ == "__main__":
    asyncio.run(_main())
