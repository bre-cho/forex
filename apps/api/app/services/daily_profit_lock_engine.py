from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.daily_trading_state import DailyTradingStateService
from app.services.safety_ledger import SafetyLedgerService
from app.services.policy_service import PolicyService


class DailyProfitLockEngine:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.daily = DailyTradingStateService(db)
        self.ledger = SafetyLedgerService(db)
        self.policy = PolicyService(db)

    async def evaluate_and_apply(
        self,
        *,
        bot_instance_id: str,
        equity: float,
    ) -> dict[str, Any]:
        state = await self.daily.recompute_from_broker_equity(bot_instance_id, equity)
        active_policy = await self.policy.get_active_policy(bot_instance_id)
        snapshot = dict(active_policy.policy_snapshot) if active_policy is not None else {}
        cfg = snapshot.get("daily_take_profit") if isinstance(snapshot, dict) else None
        enabled = bool(isinstance(cfg, dict) and cfg.get("enabled", False))
        if not enabled:
            await self.db.commit()
            return {"locked": bool(state.locked), "reason": state.lock_reason, "event": None}

        from trading_core.risk.daily_profit_policy import resolve_daily_take_profit_target

        target = resolve_daily_take_profit_target(snapshot, starting_equity=float(state.starting_equity or 0.0), daily_profit_amount=float(state.daily_profit_amount or 0.0))
        if float(state.daily_profit_amount or 0.0) < float(target or 1e18):
            await self.db.commit()
            return {"locked": bool(state.locked), "reason": state.lock_reason, "event": None}

        state.locked = True
        state.lock_reason = "daily_take_profit_hit"
        lock_action = str(cfg.get("after_hit_action", "stop_new_orders") or "stop_new_orders")
        await self.db.commit()
        await self.ledger.record_daily_lock_event(
            bot_instance_id=bot_instance_id,
            event_type="daily_tp_hit",
            lock_action=lock_action,
            reason="daily_take_profit_hit",
            payload={
                "daily_profit_amount": float(state.daily_profit_amount or 0.0),
                "target": float(target or 0.0),
                "equity": float(state.current_equity or 0.0),
            },
        )
        return {
            "locked": True,
            "reason": "daily_take_profit_hit",
            "event": "daily_tp_hit",
            "lock_action": lock_action,
            "target": float(target or 0.0),
        }
