from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession


class ReconciliationDaemonHealthService:
    """Small health adapter for live-start preflight daemon liveness check."""

    @staticmethod
    async def is_healthy(db: AsyncSession | None = None, *, max_age_seconds: float = 60.0) -> bool:
        if db is not None:
            try:
                from app.services.worker_heartbeat_service import WorkerHeartbeatService

                svc = WorkerHeartbeatService(db)
                if await svc.is_worker_healthy(
                    worker_name="reconciliation_daemon",
                    max_age_seconds=max_age_seconds,
                ):
                    return True
            except Exception:
                # Fallback to module-level flag when migrations are not applied yet.
                pass
        try:
            from app.workers import reconciliation_daemon as daemon_mod

            return bool(getattr(daemon_mod, "_daemon_running", False))
        except Exception:
            return False
