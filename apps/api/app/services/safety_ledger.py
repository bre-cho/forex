from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.models import (
    BrokerAccountSnapshot,
    BrokerExecutionReceipt,
    BrokerOrderAttempt,
    BrokerOrderEvent,
    BrokerReconciliationRun,
    DailyTradingState,
    DailyLockEvent,
    FrozenGateContext,
    OrderIdempotencyReservation,
    OrderStateTransition,
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
        frozen_context_id: str | None = None,
        gate_context_hash: str | None = None,
        status: str = "PENDING_SUBMIT",
        auto_commit: bool = True,
    ) -> BrokerOrderAttempt:
        stmt = select(BrokerOrderAttempt).where(
            BrokerOrderAttempt.bot_instance_id == bot_instance_id,
            BrokerOrderAttempt.idempotency_key == idempotency_key,
        )
        existing = (await self.db.execute(stmt.limit(1))).scalar_one_or_none()
        if existing is not None:
            changed = False
            if gate_context_hash and str(getattr(existing, "gate_context_hash", "") or "") != str(gate_context_hash):
                existing.gate_context_hash = str(gate_context_hash)
                changed = True
            if frozen_context_id and str(getattr(existing, "frozen_context_id", "") or "") != str(frozen_context_id):
                existing.frozen_context_id = str(frozen_context_id)
                changed = True
            if changed:
                existing.updated_at = datetime.now(timezone.utc)
                if auto_commit:
                    await self.db.commit()
                    await self.db.refresh(existing)
                else:
                    await self.db.flush()
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
            frozen_context_id=str(frozen_context_id or "") or None,
            gate_context_hash=str(gate_context_hash or "") or None,
            status=status,
            current_state="INTENT_CREATED",
        )
        self.db.add(row)
        if auto_commit:
            await self.db.commit()
            await self.db.refresh(row)
        else:
            await self.db.flush()
        return row

    async def get_order_attempt(self, bot_instance_id: str, idempotency_key: str) -> BrokerOrderAttempt | None:
        stmt = select(BrokerOrderAttempt).where(
            BrokerOrderAttempt.bot_instance_id == bot_instance_id,
            BrokerOrderAttempt.idempotency_key == idempotency_key,
        )
        return (await self.db.execute(stmt.limit(1))).scalar_one_or_none()

    async def update_order_attempt(
        self,
        *,
        bot_instance_id: str,
        idempotency_key: str,
        status: str,
        current_state: str | None = None,
        broker_order_id: str | None = None,
        error_message: str | None = None,
        gate_context_hash: str | None = None,
        frozen_context_id: str | None = None,
        auto_commit: bool = True,
    ) -> BrokerOrderAttempt | None:
        stmt = select(BrokerOrderAttempt).where(
            BrokerOrderAttempt.bot_instance_id == bot_instance_id,
            BrokerOrderAttempt.idempotency_key == idempotency_key,
        )
        row = (await self.db.execute(stmt.limit(1))).scalar_one_or_none()
        if row is None:
            return None
        row.status = str(status or row.status)
        if current_state is not None:
            row.current_state = str(current_state or row.current_state)
        if broker_order_id is not None:
            row.broker_order_id = broker_order_id
        if error_message is not None:
            row.error_message = error_message
        if gate_context_hash is not None:
            row.gate_context_hash = str(gate_context_hash or "") or None
        if frozen_context_id is not None:
            row.frozen_context_id = str(frozen_context_id or "") or None
        row.updated_at = datetime.now(timezone.utc)
        if auto_commit:
            await self.db.commit()
            await self.db.refresh(row)
        else:
            await self.db.flush()
        return row

    async def record_daily_lock_event(
        self,
        *,
        bot_instance_id: str,
        event_type: str,
        lock_action: str,
        reason: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> DailyLockEvent:
        row = DailyLockEvent(
            bot_instance_id=bot_instance_id,
            event_type=event_type,
            lock_action=lock_action,
            reason=reason,
            payload=payload or {},
        )
        self.db.add(row)
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

    async def record_order_state_transition(
        self,
        *,
        bot_instance_id: str,
        signal_id: str,
        idempotency_key: str,
        from_state: str | None,
        to_state: str,
        event_type: str,
        detail: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> OrderStateTransition:
        row = OrderStateTransition(
            bot_instance_id=bot_instance_id,
            signal_id=signal_id,
            idempotency_key=idempotency_key,
            from_state=from_state,
            to_state=to_state,
            event_type=event_type,
            detail=detail,
            payload=payload or {},
        )
        self.db.add(row)
        try:
            await self.db.commit()
            await self.db.refresh(row)
            return row
        except IntegrityError:
            await self.db.rollback()
            existing = (
                (
                    await self.db.execute(
                        select(OrderStateTransition)
                        .where(
                            OrderStateTransition.bot_instance_id == bot_instance_id,
                            OrderStateTransition.idempotency_key == idempotency_key,
                            OrderStateTransition.event_type == event_type,
                            OrderStateTransition.to_state == to_state,
                        )
                        .order_by(OrderStateTransition.id.desc())
                        .limit(1)
                    )
                )
                .scalar_one_or_none()
            )
            if existing is not None:
                return existing
            raise

    async def list_order_state_transitions(self, bot_instance_id: str, limit: int = 100) -> list[OrderStateTransition]:
        return (
            (
                await self.db.execute(
                    select(OrderStateTransition)
                    .where(OrderStateTransition.bot_instance_id == bot_instance_id)
                    .order_by(OrderStateTransition.created_at.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )

    async def record_execution_receipt(
        self,
        *,
        bot_instance_id: str,
        idempotency_key: str,
        broker: str,
        broker_order_id: str | None,
        broker_position_id: str | None,
        broker_deal_id: str | None,
        client_order_id: str | None = None,
        frozen_context_id: str | None = None,
        fill_status: str,
        requested_volume: float,
        filled_volume: float,
        avg_fill_price: float | None,
        commission: float,
        raw_response: dict[str, Any] | None = None,
        account_id: str | None = None,
        server_time: float | None = None,
        latency_ms: float | None = None,
        raw_response_hash: str | None = None,
        submit_status: str | None = None,
        auto_commit: bool = True,
    ) -> BrokerExecutionReceipt:
        payload = raw_response or {}
        payload_hash = raw_response_hash
        if not payload_hash:
            canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            payload_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        row = BrokerExecutionReceipt(
            bot_instance_id=bot_instance_id,
            idempotency_key=idempotency_key,
            frozen_context_id=str(frozen_context_id or "") or None,
            broker=broker,
            broker_order_id=broker_order_id,
            broker_position_id=broker_position_id,
            broker_deal_id=broker_deal_id,
            submit_status=submit_status,
            client_order_id=client_order_id,
            fill_status=fill_status,
            requested_volume=float(requested_volume or 0.0),
            filled_volume=float(filled_volume or 0.0),
            avg_fill_price=avg_fill_price,
            commission=float(commission or 0.0),
            account_id=account_id,
            server_time=float(server_time) if server_time is not None else None,
            latency_ms=float(latency_ms or 0.0),
            raw_response_hash=payload_hash,
            raw_response=payload,
        )
        self.db.add(row)
        if auto_commit:
            await self.db.commit()
            await self.db.refresh(row)
        else:
            await self.db.flush()
        return row

    async def record_frozen_gate_context(
        self,
        *,
        context_id: str,
        bot_instance_id: str,
        idempotency_key: str,
        context_hash: str,
        context_signature: str,
        canonical_context: dict[str, Any],
        runtime_version: str | None = None,
        policy_version_id: str | None = None,
        broker_snapshot_hash: str | None = None,
        risk_context_hash: str | None = None,
        approved_volume: float | None = None,
        approved_price: float | None = None,
        approved_sl: float | None = None,
        approved_tp: float | None = None,
        max_slippage_pips: float | None = None,
        max_price_deviation_bps: float | None = None,
    ) -> FrozenGateContext:
        existing = (
            (
                await self.db.execute(
                    select(FrozenGateContext)
                    .where(FrozenGateContext.id == context_id)
                    .limit(1)
                )
            )
            .scalar_one_or_none()
        )
        if existing is not None:
            return existing

        row = FrozenGateContext(
            id=str(context_id),
            bot_instance_id=str(bot_instance_id),
            idempotency_key=str(idempotency_key),
            context_hash=str(context_hash),
            context_signature=str(context_signature),
            canonical_context=dict(canonical_context or {}),
            runtime_version=str(runtime_version or "") or None,
            policy_version_id=str(policy_version_id or "") or None,
            broker_snapshot_hash=str(broker_snapshot_hash or "") or None,
            risk_context_hash=str(risk_context_hash or "") or None,
            approved_volume=float(approved_volume) if approved_volume is not None else None,
            approved_price=float(approved_price) if approved_price is not None else None,
            approved_sl=float(approved_sl) if approved_sl is not None else None,
            approved_tp=float(approved_tp) if approved_tp is not None else None,
            max_slippage_pips=float(max_slippage_pips) if max_slippage_pips is not None else None,
            max_price_deviation_bps=float(max_price_deviation_bps) if max_price_deviation_bps is not None else None,
        )
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def list_execution_receipts(self, bot_instance_id: str, limit: int = 100) -> list[BrokerExecutionReceipt]:
        return (
            (
                await self.db.execute(
                    select(BrokerExecutionReceipt)
                    .where(BrokerExecutionReceipt.bot_instance_id == bot_instance_id)
                    .order_by(BrokerExecutionReceipt.created_at.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )

    async def record_broker_account_snapshot(
        self,
        *,
        bot_instance_id: str,
        broker: str,
        account_id: str | None,
        balance: float | None,
        equity: float | None,
        margin: float | None,
        free_margin: float | None,
        margin_level: float | None,
        currency: str | None,
        raw_response: dict[str, Any] | None = None,
    ) -> BrokerAccountSnapshot:
        row = BrokerAccountSnapshot(
            bot_instance_id=bot_instance_id,
            broker=broker,
            account_id=account_id,
            balance=balance,
            equity=equity,
            margin=margin,
            free_margin=free_margin,
            margin_level=margin_level,
            currency=currency,
            raw_response=raw_response or {},
        )
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def list_broker_account_snapshots(
        self,
        bot_instance_id: str,
        limit: int = 100,
    ) -> list[BrokerAccountSnapshot]:
        return (
            (
                await self.db.execute(
                    select(BrokerAccountSnapshot)
                    .where(BrokerAccountSnapshot.bot_instance_id == bot_instance_id)
                    .order_by(BrokerAccountSnapshot.created_at.desc())
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
        transitions = await self.list_order_state_transitions(bot_instance_id, limit)
        receipts = await self.list_execution_receipts(bot_instance_id, limit)
        account_snapshots = await self.list_broker_account_snapshots(bot_instance_id, limit)
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
            "order_state_transitions": transitions,
            "execution_receipts": receipts,
            "broker_account_snapshots": account_snapshots,
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
