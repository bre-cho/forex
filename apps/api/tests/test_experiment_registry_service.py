from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.db import Base
from app.services.experiment_registry_service import ExperimentRegistryService


@pytest.mark.asyncio
async def test_experiment_registry_create_and_advance() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_maker() as session:
        svc = ExperimentRegistryService(session)

        created = await svc.create_experiment(
            bot_instance_id="bot-1",
            strategy_snapshot={"name": "wave"},
            policy_snapshot={"risk": 1},
            note="first",
            created_by="user-1",
        )
        assert created.version == 1
        assert created.stage == "DRAFT"

        advanced = await svc.advance_stage(
            bot_instance_id="bot-1",
            version=1,
            stage="DEMO_TEST",
            metrics_snapshot={"winrate": 0.56},
            note="demo ok",
            actor_user_id="user-2",
        )
        assert advanced is not None
        assert advanced.stage == "DEMO_TEST"

        rows = await svc.list_experiments("bot-1", limit=10)
        assert len(rows) == 1
        assert rows[0].metrics_snapshot["winrate"] == 0.56
