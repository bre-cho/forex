"""DailyLockRuntimeController — applies after-hit-action when daily TP/loss lock fires.

When DailyProfitLockEngine sets ``state.locked = True``, this controller is responsible
for translating ``lock_action`` into actual runtime effects:

- ``stop_new_orders``    → pause new order submission for the bot
- ``close_all_and_stop`` → close all open broker positions, then stop the bot
- ``reduce_risk_only``   → switch the runtime into reduce-only risk mode

The controller is deliberately thin — it delegates to the bot runtime registry
and broker provider.  It does NOT write to DB itself; that is done by the
DailyProfitLockEngine and the bot service.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


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
    on_action_completed:
        Optional async callback called with ``{"bot_id", "lock_action", "outcome", "detail"}``
        after the action attempt (success or failure).
    """

    def __init__(
        self,
        *,
        provider: Any,
        runtime_registry: Any,
        on_action_completed=None,
    ) -> None:
        self._provider = provider
        self._registry = runtime_registry
        self._on_action_completed = on_action_completed

    async def apply_lock_action(
        self,
        bot_id: str,
        lock_action: str,
        *,
        symbol: Optional[str] = None,
    ) -> dict:
        """Apply the runtime effect of a daily lock.

        Returns a result dict with keys: ``bot_id``, ``lock_action``,
        ``outcome`` (``ok`` | ``partial`` | ``error``), ``detail``.
        """
        action = str(lock_action or "stop_new_orders").strip().lower()
        result = {"bot_id": bot_id, "lock_action": action, "outcome": "ok", "detail": ""}

        try:
            if action == "stop_new_orders":
                await self._pause_new_orders(bot_id)
                result["detail"] = "new_orders_paused"

            elif action == "close_all_and_stop":
                close_result = await self._close_all_positions(bot_id, symbol=symbol)
                result["detail"] = f"closed_positions:{close_result}"
                await self._stop_bot(bot_id)

            elif action == "reduce_risk_only":
                await self._set_risk_mode(bot_id, "reduce_only")
                result["detail"] = "reduce_only_mode_set"

            else:
                logger.warning("DailyLockRuntimeController: unknown lock_action=%s for bot=%s", action, bot_id)
                result["outcome"] = "partial"
                result["detail"] = f"unknown_lock_action:{action}"

        except Exception as exc:
            logger.error("DailyLockRuntimeController error bot=%s action=%s: %s", bot_id, action, exc)
            result["outcome"] = "error"
            result["detail"] = str(exc)

        if self._on_action_completed is not None:
            try:
                await self._on_action_completed(result)
            except Exception as exc:
                logger.error("DailyLockRuntimeController on_action_completed hook failed: %s", exc)

        return result

    # ------------------------------------------------------------------
    # Delegate helpers
    # ------------------------------------------------------------------

    async def _pause_new_orders(self, bot_id: str) -> None:
        if hasattr(self._registry, "pause_new_orders"):
            await self._registry.pause_new_orders(bot_id)
        else:
            logger.warning("registry.pause_new_orders not available — bot %s will not be paused", bot_id)

    async def _stop_bot(self, bot_id: str) -> None:
        if hasattr(self._registry, "stop"):
            await self._registry.stop(bot_id)
        else:
            logger.warning("registry.stop not available — bot %s will not be stopped", bot_id)

    async def _set_risk_mode(self, bot_id: str, mode: str) -> None:
        if hasattr(self._registry, "set_risk_mode"):
            await self._registry.set_risk_mode(bot_id, mode)
        else:
            logger.warning("registry.set_risk_mode not available — bot %s mode not changed", bot_id)

    async def _close_all_positions(self, bot_id: str, symbol: Optional[str] = None) -> str:
        provider = self._provider
        if hasattr(provider, "close_all_positions"):
            results = await provider.close_all_positions(symbol)
            count = len(results) if results else 0
            logger.info("DailyLockRuntimeController: closed %d positions for bot %s", count, bot_id)
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
        return str(closed)
