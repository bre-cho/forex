from __future__ import annotations

import pytest

from execution_service.reconciliation_worker import ReconciliationWorker


@pytest.mark.asyncio
async def test_reconciliation_error_emits_critical_incident() -> None:
    incidents: list[dict] = []

    async def _bad_positions():
        raise RuntimeError("provider_sync_failed")

    async def _db_positions():
        return []

    async def _on_incident(payload: dict) -> None:
        incidents.append(payload)

    worker = ReconciliationWorker(
        bot_instance_id="bot-1",
        provider=type("Provider", (), {"get_open_positions": _bad_positions})(),
        get_db_open_trades=_db_positions,
        on_incident=_on_incident,
    )

    result = await worker.run_once()

    assert result.status == "error"
    assert incidents
    assert incidents[0]["incident_type"] == "reconciliation_runtime_error"
    assert incidents[0]["severity"] == "critical"