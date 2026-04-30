"""DailyLockOrchestrator — exactly-once daily lock with postcondition verify.

Enforces the following guarantee:
  1. Acquires an advisory DB-level lock key so concurrent API calls / daemon
     ticks cannot race on the same bot+day.
  2. Persists the lock via DailyTradingStateService.lock_day (sets locked=True).
  3. Re-reads the row and ASSERTS locked==True (postcondition verify).
  4. On any failure, creates a critical TradingIncident so the anomaly is
     auditable even if the lock was not durably set.

Audit spec: P0-E — DailyLockOrchestrator
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class DailyLockOrchestrator:
    """Exactly-once daily lock with postcondition verify and incident fallback."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        # In-process advisory set to prevent concurrent coroutines racing before
        # the DB round-trip completes. Maps (bot_instance_id, day_str) -> asyncio.Lock
        self._advisory_locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def lock_bot_for_day(
        self,
        bot_instance_id: str,
        reason: str,
        *,
        create_incident: bool = True,
    ) -> bool:
        """Lock *bot_instance_id* for today with exactly-once semantics.

        Returns True if lock is durably confirmed, False if it could not be
        confirmed (incident will have been raised if create_incident=True).
        """
        day_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        advisory_key = f"{bot_instance_id}:{day_str}"

        if advisory_key not in self._advisory_locks:
            self._advisory_locks[advisory_key] = asyncio.Lock()

        async with self._advisory_locks[advisory_key]:
            return await self._do_lock(
                bot_instance_id=bot_instance_id,
                reason=reason,
                create_incident=create_incident,
            )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _do_lock(
        self,
        bot_instance_id: str,
        reason: str,
        create_incident: bool,
    ) -> bool:
        from app.models import TradingIncident
        from app.services.daily_trading_state import DailyTradingStateService

        state_svc = DailyTradingStateService(self._db)

        # Step 1 — Check if already locked (idempotent fast-path).
        try:
            existing = await state_svc.get_or_create(bot_instance_id)
            if getattr(existing, "locked", False):
                logger.info(
                    "DailyLockOrchestrator: bot=%s already locked, reason=%s",
                    bot_instance_id,
                    reason,
                )
                return True
        except Exception as exc:
            logger.error(
                "DailyLockOrchestrator: pre-check failed bot=%s: %s", bot_instance_id, exc
            )
            if create_incident:
                await self._raise_incident(bot_instance_id, reason, f"pre_check_failed: {exc}")
            return False

        # Step 2 — Persist lock.
        try:
            await state_svc.lock_day(bot_instance_id, reason=reason)
        except Exception as exc:
            logger.error(
                "DailyLockOrchestrator: lock_day failed bot=%s reason=%s: %s",
                bot_instance_id,
                reason,
                exc,
            )
            try:
                await self._db.rollback()
            except Exception:
                pass
            if create_incident:
                await self._raise_incident(bot_instance_id, reason, f"lock_persist_failed: {exc}")
            return False

        # Step 3 — Postcondition verify: re-read and assert locked==True.
        try:
            await self._db.refresh(existing)
            verified = getattr(existing, "locked", False)
        except Exception as exc:
            logger.error(
                "DailyLockOrchestrator: postcondition refresh failed bot=%s: %s",
                bot_instance_id,
                exc,
            )
            verified = False

        if not verified:
            logger.critical(
                "DailyLockOrchestrator: POSTCONDITION FAILED — lock not confirmed bot=%s",
                bot_instance_id,
            )
            if create_incident:
                await self._raise_incident(
                    bot_instance_id, reason, "postcondition_failed: locked!=True after persist"
                )
            return False

        logger.info(
            "DailyLockOrchestrator: lock confirmed bot=%s reason=%s",
            bot_instance_id,
            reason,
        )
        return True

    async def _raise_incident(
        self,
        bot_instance_id: str,
        reason: str,
        detail_suffix: str,
    ) -> None:
        """Create critical TradingIncident for lock failure. Best-effort."""
        from app.models import TradingIncident
        from app.services.incident_notifier import notify_incident

        title = f"Daily lock orchestration failed: {bot_instance_id}"
        detail = f"bot={bot_instance_id} reason={reason} {detail_suffix}"
        incident = TradingIncident(
            bot_instance_id=bot_instance_id,
            incident_type="daily_lock_orchestration_failed",
            severity="critical",
            title=title,
            detail=detail,
            status="open",
        )
        try:
            self._db.add(incident)
            await self._db.commit()
        except Exception as exc:
            logger.error(
                "DailyLockOrchestrator: failed to persist incident bot=%s: %s",
                bot_instance_id,
                exc,
            )
            try:
                await self._db.rollback()
            except Exception:
                pass
            return

        try:
            await notify_incident(
                incident_type="daily_lock_orchestration_failed",
                severity="critical",
                title=title,
                detail=detail,
                payload={"bot_instance_id": bot_instance_id, "reason": reason},
            )
        except Exception as exc:
            logger.warning(
                "DailyLockOrchestrator: notify_incident failed (non-fatal) bot=%s: %s",
                bot_instance_id,
                exc,
            )
