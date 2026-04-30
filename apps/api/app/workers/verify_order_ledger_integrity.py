from __future__ import annotations

import asyncio
import json
import logging
import sys

from app.core.db import AsyncSessionLocal
from app.services.order_ledger_integrity_service import OrderLedgerIntegrityService
from app.services.safety_ledger import SafetyLedgerService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s", stream=sys.stdout)
logger = logging.getLogger("order_ledger_integrity")


async def run_once() -> dict:
    async with AsyncSessionLocal() as db:
        svc = OrderLedgerIntegrityService(db)
        report = await svc.run()

        if report.get("critical_count", 0) > 0:
            ledger = SafetyLedgerService(db)
            for issue in report.get("issues", []):
                if str(issue.get("severity") or "") != "critical":
                    continue
                bot_id = str(issue.get("bot_instance_id") or "")
                if not bot_id:
                    continue
                await ledger.create_incident(
                    bot_instance_id=bot_id,
                    incident_type="order_ledger_integrity_violation",
                    severity="critical",
                    title="Order ledger integrity violation",
                    detail=json.dumps(issue, ensure_ascii=True),
                )

        return report


async def _main() -> None:
    report = await run_once()
    logger.info("order_ledger_integrity report=%s", json.dumps(report, ensure_ascii=True))


if __name__ == "__main__":
    asyncio.run(_main())
