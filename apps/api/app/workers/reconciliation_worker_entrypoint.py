"""Reconciliation worker standalone entrypoint.

Run as: python -m app.workers.reconciliation_worker_entrypoint

This entrypoint is used in the production 'reconciliation-worker' Docker service
(docker-compose.prod.yml) to run the unknown-order daemon in an isolated process,
independent of the API server. This prevents API replica restarts from interrupting
in-flight reconcile cycles.

Audit spec: P0-F — Production worker separation.
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
logger = logging.getLogger("reconciliation_worker")


async def _main() -> None:
    # Bootstrap DB engine / settings before importing daemon modules.
    from app.core.db import init_db  # noqa: F401 — side-effect: creates engine

    stop_event = asyncio.Event()

    def _handle_signal(sig: int, _frame: object) -> None:
        logger.info("reconciliation_worker: received signal %s, shutting down", sig)
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    poll_interval = float(os.getenv("RECON_POLL_INTERVAL_SECONDS", "15"))
    logger.info("reconciliation_worker: starting, poll_interval=%.1fs", poll_interval)

    from app.workers.reconciliation_daemon import run_reconciliation_daemon

    await run_reconciliation_daemon(
        poll_interval=poll_interval,
        stop_event=stop_event,
    )
    logger.info("reconciliation_worker: stopped cleanly")


if __name__ == "__main__":
    asyncio.run(_main())
