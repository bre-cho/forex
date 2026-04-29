"""DailyLockRuntimeController — applies after-hit-action when daily TP/loss lock fires.

When DailyProfitLockEngine sets ``state.locked = True``, this controller is responsible
for translating ``lock_action`` into actual runtime effects:

- ``stop_new_orders``    → pause new order submission for the bot
- ``close_all_and_stop`` → close all open broker positions, then stop the bot
- ``reduce_risk_only``   → switch the runtime into reduce-only risk mode

P0.3: Every action is recorded exactly-once in ``daily_lock_actions``.  If a record
already exists with status ``completed``, the action is skipped (idempotent).
If status is ``failed``, the action may be retried up to ``max_attempts``.

The controller is deliberately thin — it delegates to the bot runtime registry
and broker provider.  It does NOT write to DB itself; that is done by the
DailyProfitLockEngine and the bot service.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailyLockAction

logger = logging.getLogger(__name__)

_MAX_ACTION_ATTEMPTS = 3


class DailyLockRuntimeController:
    """Executes runtime actions triggered by a daily lock event.

    Parameters
    ----------
    provider:
        Broker provider for the bot.  Used to close positions when
        ``lock_action == "close_all_and_stop"``.
    runtime_registry:
        Runtime registry that tracks running bots.  Must expose:
            - ``pause_new_orders(bot_id)``  — async
            - ``stop(bot_id)``              — async
            - ``set_risk_mode(bot_id, mode)`` — async
        All methods are called with ``await`` and may raise.
    db:
        Optional AsyncSession for exactly-once action log.  If None, no
        idempotency logging is performed (e.g. tests).
    on_action_completed:
        Optional async callback called with ``{"bot_id", "lock_action", "outcome", "detail"}``
        after the action attempt (success or failure).
    """

    def __init__(
        self,
        *,
        provider: Any,
        runtime_registry: Any,
        db: Optional[AsyncSession] = None,
        on_action_completed=None,
    ) -> None:
        self._provider = provider
        self._registry = runtime_registry
        self._db = db
        self._on_action_completed = on_action_completed

    async def apply_lock_action(
        self,
        bot_id: str,
        lock_action: str,
        *,
        symbol: Optional[str] = None,
        lock_reason: Optional[str] = None,
        trading_day: Optional[date] = None,
    ) -> dict:
        """Apply the runtime effect of a daily lock.

        Returns a result dict with keys: ``bot_id``, ``lock_action``,
        ``outcome`` (``ok`` | ``partial`` | ``error``), ``detail``.

        P0.3: Uses daily_lock_actions for exactly-once semantics.  If
        DB is configured, duplicate calls for the same (bot_id, trading_day,
        lock_action) with status=completed are no-ops.
        """
        action = str(lock_action or "stop_new_orders").strip().lower()
        result = {"bot_id": bot_id, "lock_action": action, "outcome": "ok", "detail": ""}
        today = trading_day or date.today()

        # P0.3 — Exactly-once: check/create action log record under DB lock
        action_row: Optional[DailyLockAction] = None
        if self._db is not None:
            action_row = await self._get_or_create_action_row(bot_id, today, action, lock_reason)
            if action_row is None:
                # Could not acquire (another worker holds it)
                result["outcome"] = "skipped"
                result["detail"] = "action_already_claimed_by_another_worker"
                return result
            if action_row.status == "completed":
                result["outcome"] = "skipped"
                result["detail"] = "action_already_completed"
                return result
            if int(action_row.attempts or 0) >= _MAX_ACTION_ATTEMPTS:
                result["outcome"] = "error"
                result["detail"] = f"max_attempts_exceeded:{action_row.last_error}"
                return result

        try:
            if action == "stop_new_orders":
                await self._pause_new_orders(bot_id)
                result["detail"] = "new_orders_paused"

            elif action == "close_all_and_stop":
                positions_before = 0
                try:
                    open_pos = await self._provider.get_open_positions()
                    positions_before = len(open_pos or [])
                except Exception:
                    pass
                close_result = await self._close_all_positions(bot_id, symbol=symbol)
                result["detail"] = f"closed_positions:{close_result}"
                await self._stop_bot(bot_id)
                if action_row is not None:
                    action_row.positions_before = positions_before
                    action_row.positions_after = 0

            elif action == "reduce_risk_only":
                await self._set_risk_mode(bot_id, "reduce_only")
                result["detail"] = "reduce_only_mode_set"

            else:
                logger.warning("DailyLockRuntimeController: unknown lock_action=%s for bot=%s", action, bot_id)
                result["outcome"] = "partial"
                result["detail"] = f"unknown_lock_action:{action}"

            # Mark completed in DB
            if action_row is not None and self._db is not None:
                action_row.status = "completed"
                action_row.attempts = int(action_row.attempts or 0) + 1
                action_row.action_detail = result
                action_row.action_hash = self._compute_hash(result)
                action_row.completed_at = datetime.now(timezone.utc)
                action_row.updated_at = datetime.now(timezone.utc)
                await self._db.commit()

        except Exception as exc:
            logger.error("DailyLockRuntimeController error bot=%s action=%s: %s", bot_id, action, exc)
            result["outcome"] = "error"
            result["detail"] = str(exc)
            if action_row is not None and self._db is not None:
                action_row.status = "failed"
                action_row.attempts = int(action_row.attempts or 0) + 1
                action_row.last_error = str(exc)
                action_row.updated_at = datetime.now(timezone.utc)
                try:
                    await self._db.commit()
                except Exception:
                    await self._db.rollback()

        if self._on_action_completed is not None:
            try:
                await self._on_action_completed(result)
            except Exception as exc:
                logger.error("DailyLockRuntimeController on_action_completed hook failed: %s", exc)

        return result

    # ------------------------------------------------------------------
    # Exactly-once helpers
    # ------------------------------------------------------------------

    async def _get_or_create_action_row(
        self,
        bot_id: str,
        trading_day: date,
        lock_action: str,
        lock_reason: Optional[str],
    ) -> Optional[DailyLockAction]:
        """Return existing or newly-created DailyLockAction row.

        Uses SELECT FOR UPDATE SKIP LOCKED to prevent double-execution.
        Returns None if locked by another worker.
        """
        try:
            result = await self._db.execute(
                select(DailyLockAction)
                .where(
                    DailyLockAction.bot_instance_id == bot_id,
                    DailyLockAction.trading_day == trading_day,
                    DailyLockAction.lock_action == lock_action,
                )
                .with_for_update(skip_locked=True)
            )
            row = result.scalar_one_or_none()
            if row is not None:
                return row

            row = DailyLockAction(
                bot_instance_id=bot_id,
                trading_day=trading_day,
                lock_reason=lock_reason,
                lock_action=lock_action,
                status="running",
                attempts=0,
            )
            self._db.add(row)
            await self._db.flush()
            return row
        except Exception as exc:
            logger.error("DailyLockRuntimeController: action row error: %s", exc)
            try:
                await self._db.rollback()
            except Exception:
                pass
            return None

    @staticmethod
    def _compute_hash(data: dict) -> str:
        payload = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()[:32]

    # ------------------------------------------------------------------
    # Delegate helpers
    # ------------------------------------------------------------------

    async def _pause_new_orders(self, bot_id: str) -> None:
        if hasattr(self._registry, "pause_new_orders"):
            await self._registry.pause_new_orders(bot_id)
        else:
            raise RuntimeError("registry_pause_new_orders_unavailable")

    async def _stop_bot(self, bot_id: str) -> None:
        if hasattr(self._registry, "stop"):
            await self._registry.stop(bot_id)
        else:
            raise RuntimeError("registry_stop_unavailable")

    async def _set_risk_mode(self, bot_id: str, mode: str) -> None:
        if hasattr(self._registry, "set_risk_mode"):
            await self._registry.set_risk_mode(bot_id, mode)
        else:
            raise RuntimeError("registry_set_risk_mode_unavailable")

    async def _close_all_positions(self, bot_id: str, symbol: Optional[str] = None) -> str:
        provider = self._provider
        if hasattr(provider, "close_all_positions"):
            results = await provider.close_all_positions(symbol)
            count = len(results) if results else 0
            logger.info("DailyLockRuntimeController: closed %d positions for bot %s", count, bot_id)
            remaining = await provider.get_open_positions()
            remaining_count = len([p for p in (remaining or []) if not symbol or str(p.get("symbol") or "").upper() == str(symbol).upper()])
            if remaining_count > 0:
                raise RuntimeError(f"close_all_positions_incomplete:{remaining_count}")
            return str(count)
        # Fallback: close individual positions
        try:
            positions = await provider.get_open_positions()
        except Exception as exc:
            logger.error("get_open_positions failed for bot %s: %s", bot_id, exc)
            return "error"
        closed = 0
        for pos in positions or []:
            if symbol and str(pos.get("symbol") or "").upper() != symbol.upper():
                continue
            pos_id = str(pos.get("position_id") or pos.get("id") or "")
            if not pos_id:
                continue
            try:
                await provider.close_position(pos_id)
                closed += 1
            except Exception as exc:
                logger.error("close_position %s failed for bot %s: %s", pos_id, bot_id, exc)
        remaining = await provider.get_open_positions()
        remaining_count = len([p for p in (remaining or []) if not symbol or str(p.get("symbol") or "").upper() == str(symbol).upper()])
        if remaining_count > 0:
            raise RuntimeError(f"close_positions_fallback_incomplete:{remaining_count}")
        return str(closed)
