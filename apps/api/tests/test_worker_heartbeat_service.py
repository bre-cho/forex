from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import WorkerHeartbeat
from app.services.worker_heartbeat_service import WorkerHeartbeatService


@pytest.mark.asyncio
async def test_worker_heartbeat_mark_and_health() -> None:
    pytest.importorskip("aiosqlite")

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(WorkerHeartbeat.__table__.create)

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        svc = WorkerHeartbeatService(db)

        await svc.beat(
            worker_name="reconciliation_daemon",
            worker_id="worker-1",
            status="running",
            detail={"phase": "poll"},
        )
        healthy = await svc.is_worker_healthy(
            worker_name="reconciliation_daemon",
            max_age_seconds=60.0,
        )
        assert healthy is True

        await svc.beat(
            worker_name="reconciliation_daemon",
            worker_id="worker-1",
            status="stopped",
            detail={"phase": "stop"},
        )
        healthy_after_stop = await svc.is_worker_healthy(
            worker_name="reconciliation_daemon",
            max_age_seconds=60.0,
        )
        assert healthy_after_stop is False

    await engine.dispose()
