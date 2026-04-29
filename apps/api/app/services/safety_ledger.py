from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.models import (
    BrokerOrderAttempt,
    BrokerOrderEvent,
    BrokerReconciliationRun,
    DailyTradingState,
    OrderIdempotencyReservation,
    PreExecutionGateEvent,
    TradingDecisionLedger,
    TradingIncident,
)


class SafetyLedgerService:
    """Persists decision/gate/order events for live trading safety."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def record_brain_cycle(self, bot_instance_id: str, payload: dict[str, Any]) -> TradingDecisionLedger:
        signal_id = str(payload.get("selected_signal", {}).get("signal_id") or payload.get("cycle_id") or "")
        row = TradingDecisionLedger(
            bot_instance_id=bot_instance_id,
            signal_id=signal_id,
            cycle_id=str(payload.get("cycle_id") or ""),
            brain_action=str(payload.get("action") or "BLOCK"),
            brain_reason=str(payload.get("reason") or ""),
            brain_score=float(payload.get("final_score") or 0.0),
            stage_decisions=payload.get("stage_decisions") or [],
            policy_snapshot=payload.get("policy_snapshot") or {},
        )
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def reserve_idempotency(
        self,
        bot_instance_id: str,
        signal_id: str,
        idempotency_key: str,
        brain_cycle_id: str | None = None,
    ) -> bool:
        row = OrderIdempotencyReservation(
            bot_instance_id=bot_instance_id,
            signal_id=signal_id,
            idempotency_key=idempotency_key,
            brain_cycle_id=brain_cycle_id,
            status="reserved",
        )
        self.db.add(row)
        try:
            await self.db.commit()
            return True
        except IntegrityError:
            await self.db.rollback()
            return False

    async def has_idempotency_reservation(
        self,
        bot_instance_id: str,
        idempotency_key: str,
        brain_cycle_id: str | None = None,
    ) -> bool:
        stmt = select(OrderIdempotencyReservation).where(
            OrderIdempotencyReservation.bot_instance_id == bot_instance_id,
            OrderIdempotencyReservation.idempotency_key == idempotency_key,
        )
        if brain_cycle_id:
            stmt = stmt.where(OrderIdempotencyReservation.brain_cycle_id == brain_cycle_id)
        result = await self.db.execute(stmt.limit(1))
        return result.scalar_one_or_none() is not None

    async def mark_idempotency_status(
        self,
        bot_instance_id: str,
        idempotency_key: str,
        status: str,
        brain_cycle_id: str | None = None,
    ) -> bool:
        stmt = select(OrderIdempotencyReservation).where(
            OrderIdempotencyReservation.bot_instance_id == bot_instance_id,
            OrderIdempotencyReservation.idempotency_key == idempotency_key,
        )
        if brain_cycle_id:
            stmt = stmt.where(OrderIdempotencyReservation.brain_cycle_id == brain_cycle_id)
        result = await self.db.execute(stmt.limit(1))
        row = result.scalar_one_or_none()
        if row is None:
            return False
        row.status = str(status or row.status)
        row.updated_at = datetime.now(timezone.utc)
        await self.db.commit()
        return True

    async def record_gate_event(self, payload: dict[str, Any]) -> PreExecutionGateEvent:
        row = PreExecutionGateEvent(
            bot_instance_id=str(payload.get("bot_instance_id") or ""),
            signal_id=str(payload.get("signal_id") or ""),
            idempotency_key=str(payload.get("idempotency_key") or payload.get("signal_id") or ""),
            gate_action=str(payload.get("gate_action") or "BLOCK"),
            gate_reason=str(payload.get("gate_reason") or "unknown"),
            gate_details=payload.get("gate_details") or {},
        )
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def record_broker_order_event(self, payload: dict[str, Any], event_type: str) -> BrokerOrderEvent:
        row = BrokerOrderEvent(
            bot_instance_id=str(payload.get("bot_instance_id") or ""),
            broker_order_id=str(payload.get("broker_order_id") or ""),
            event_type=event_type,
            symbol=str(payload.get("symbol") or ""),
            side=str(payload.get("side") or ""),
            volume=float(payload.get("volume") or 0.0),
            price=float(payload.get("price") or 0.0),
            payload=payload,
        )
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def create_or_get_order_attempt(
        self,
        *,
        bot_instance_id: str,
        signal_id: str,
        brain_cycle_id: str | None,
        idempotency_key: str,
        broker: str,
        symbol: str,
        side: str,
        volume: float,
        request_payload: dict[str, Any],
        status: str = "PENDING_SUBMIT",
    ) -> BrokerOrderAttempt:
        stmt = select(BrokerOrderAttempt).where(
            BrokerOrderAttempt.bot_instance_id == bot_instance_id,
            BrokerOrderAttempt.idempotency_key == idempotency_key,
        )
        existing = (await self.db.execute(stmt.limit(1))).scalar_one_or_none()
        if existing is not None:
            return existing

        row = BrokerOrderAttempt(
            bot_instance_id=bot_instance_id,
            signal_id=signal_id,
            brain_cycle_id=brain_cycle_id,
            idempotency_key=idempotency_key,
            broker=broker,
            symbol=symbol,
            side=side,
            volume=volume,
            request_payload=request_payload,
            status=status,
        )
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def update_order_attempt(
        self,
        *,
        bot_instance_id: str,
        idempotency_key: str,
        status: str,
        broker_order_id: str | None = None,
        error_message: str | None = None,
    ) -> BrokerOrderAttempt | None:
        stmt = select(BrokerOrderAttempt).where(
            BrokerOrderAttempt.bot_instance_id == bot_instance_id,
            BrokerOrderAttempt.idempotency_key == idempotency_key,
        )
        row = (await self.db.execute(stmt.limit(1))).scalar_one_or_none()
        if row is None:
            return None
        row.status = str(status or row.status)
        if broker_order_id is not None:
            row.broker_order_id = broker_order_id
        if error_message is not None:
            row.error_message = error_message
        row.updated_at = datetime.now(timezone.utc)
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def list_order_attempts(self, bot_instance_id: str, limit: int = 100) -> list[BrokerOrderAttempt]:
        return (
            (
                await self.db.execute(
                    select(BrokerOrderAttempt)
                    .where(BrokerOrderAttempt.bot_instance_id == bot_instance_id)
                    .order_by(BrokerOrderAttempt.created_at.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )


    async def record_reconciliation_run(self, payload: dict[str, Any]) -> BrokerReconciliationRun:
        row = BrokerReconciliationRun(
            bot_instance_id=str(payload.get("bot_instance_id") or ""),
            status=str(payload.get("status") or "error"),
            open_positions_broker=payload.get("open_positions_broker"),
            open_positions_db=payload.get("open_positions_db"),
            mismatches=payload.get("mismatches") or [],
            repaired=int(payload.get("repaired") or 0),
        )
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def create_incident(
        self,
        bot_instance_id: str,
        incident_type: str,
        severity: str,
        title: str,
        detail: str = "",
    ) -> TradingIncident:
        row = TradingIncident(
            bot_instance_id=bot_instance_id,
            incident_type=incident_type,
            severity=severity,
            title=title,
            detail=detail,
            status="open",
        )
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def resolve_incident(self, incident_id: int) -> TradingIncident | None:
        result = await self.db.execute(
            select(TradingIncident).where(TradingIncident.id == incident_id).limit(1)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        row.status = "resolved"
        row.resolved_at = datetime.now(timezone.utc)
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def reset_daily_lock(self, bot_instance_id: str) -> DailyTradingState | None:
        state = await self.get_daily_state(bot_instance_id)
        if state is None:
            return None
        state.locked = False
        state.lock_reason = None
        await self.db.commit()
        await self.db.refresh(state)
        return state

    async def get_daily_state(self, bot_instance_id: str, trading_day: Optional[date] = None) -> Optional[DailyTradingState]:
        trading_day = trading_day or date.today()
        result = await self.db.execute(
            select(DailyTradingState).where(
                DailyTradingState.bot_instance_id == bot_instance_id,
                DailyTradingState.trading_day == trading_day,
            )
        )
        return result.scalar_one_or_none()

    async def timeline(self, bot_instance_id: str, limit: int = 100) -> dict[str, Any]:
        decisions = (
            await self.db.execute(
                select(TradingDecisionLedger)
                .where(TradingDecisionLedger.bot_instance_id == bot_instance_id)
                .order_by(TradingDecisionLedger.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()
        gates = (
            await self.db.execute(
                select(PreExecutionGateEvent)
                .where(PreExecutionGateEvent.bot_instance_id == bot_instance_id)
                .order_by(PreExecutionGateEvent.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()
        orders = (
            await self.db.execute(
                select(BrokerOrderEvent)
                .where(BrokerOrderEvent.bot_instance_id == bot_instance_id)
                .order_by(BrokerOrderEvent.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()
        attempts = await self.list_order_attempts(bot_instance_id, limit)
        incidents = (
            await self.db.execute(
                select(TradingIncident)
                .where(TradingIncident.bot_instance_id == bot_instance_id)
                .order_by(TradingIncident.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()
        return {
            "decisions": decisions,
            "gate_events": gates,
            "order_events": orders,
            "order_attempts": attempts,
            "incidents": incidents,
        }

    async def list_reconciliation_runs(self, bot_instance_id: str, limit: int = 100) -> list[BrokerReconciliationRun]:
        return (
            (
                await self.db.execute(
                    select(BrokerReconciliationRun)
                    .where(BrokerReconciliationRun.bot_instance_id == bot_instance_id)
                    .order_by(BrokerReconciliationRun.started_at.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )

    async def list_incidents(self, bot_instance_id: str, limit: int = 100) -> list[TradingIncident]:
        return (
            (
                await self.db.execute(
                    select(TradingIncident)
                    .where(TradingIncident.bot_instance_id == bot_instance_id)
                    .order_by(TradingIncident.created_at.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
