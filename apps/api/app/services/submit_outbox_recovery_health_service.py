from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession


class SubmitOutboxRecoveryHealthService:
    """Health adapter for submit outbox recovery worker."""

    @staticmethod
    async def is_healthy(db: AsyncSession | None = None, *, max_age_seconds: float = 60.0) -> bool:
        if db is not None:
            try:
                from app.services.worker_heartbeat_service import WorkerHeartbeatService

                svc = WorkerHeartbeatService(db)
                if await svc.is_worker_healthy(
                    worker_name="submit_outbox_recovery_worker",
                    max_age_seconds=max_age_seconds,
                ):
                    return True
            except Exception:
                pass
        try:
            from app.workers import submit_outbox_recovery_worker as worker_mod

            return bool(getattr(worker_mod, "_worker_running", False))
        except Exception:
            return False
