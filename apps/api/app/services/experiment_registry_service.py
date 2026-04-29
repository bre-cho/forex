from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import StrategyExperiment


_STAGES = [
    "DRAFT",
    "PAPER_TEST",
    "DEMO_TEST",
    "MICRO_LIVE",
    "LIVE_APPROVED",
    "RETIRED",
]


class ExperimentRegistryService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_experiments(self, bot_instance_id: str, limit: int = 100) -> list[StrategyExperiment]:
        return (
            (
                await self.db.execute(
                    select(StrategyExperiment)
                    .where(StrategyExperiment.bot_instance_id == bot_instance_id)
                    .order_by(StrategyExperiment.version.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )

    async def create_experiment(
        self,
        *,
        bot_instance_id: str,
        strategy_snapshot: dict,
        policy_snapshot: dict,
        note: str | None,
        created_by: str | None,
    ) -> StrategyExperiment:
        current_max = (
            await self.db.execute(
                select(StrategyExperiment.version)
                .where(StrategyExperiment.bot_instance_id == bot_instance_id)
                .order_by(StrategyExperiment.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        version = int(current_max or 0) + 1

        row = StrategyExperiment(
            bot_instance_id=bot_instance_id,
            version=version,
            stage="DRAFT",
            strategy_snapshot=dict(strategy_snapshot or {}),
            policy_snapshot=dict(policy_snapshot or {}),
            metrics_snapshot={},
            note=note,
            created_by=created_by,
            updated_by=created_by,
        )
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def advance_stage(
        self,
        *,
        bot_instance_id: str,
        version: int,
        stage: str,
        metrics_snapshot: dict | None,
        note: str | None,
        actor_user_id: str | None,
    ) -> StrategyExperiment | None:
        target_stage = str(stage or "").upper()
        if target_stage not in _STAGES:
            raise ValueError(f"invalid_stage:{target_stage}")

        row = (
            await self.db.execute(
                select(StrategyExperiment)
                .where(
                    StrategyExperiment.bot_instance_id == bot_instance_id,
                    StrategyExperiment.version == version,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            return None

        current_stage = str(row.stage or "DRAFT").upper()
        if _STAGES.index(target_stage) < _STAGES.index(current_stage):
            raise ValueError(f"stage_regression_not_allowed:{current_stage}->{target_stage}")

        row.stage = target_stage
        if metrics_snapshot is not None:
            row.metrics_snapshot = dict(metrics_snapshot)
        if note is not None:
            row.note = note
        row.updated_by = actor_user_id
        row.updated_at = datetime.now(timezone.utc)
        await self.db.commit()
        await self.db.refresh(row)
        return row
