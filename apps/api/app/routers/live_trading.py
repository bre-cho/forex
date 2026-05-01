from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.dependencies.auth import get_current_user
from app.models import AuditLog, BotInstance, ReconciliationAttemptEvent, ReconciliationQueueItem, TradingIncident, User
from app.services.experiment_registry_service import ExperimentRegistryService
from app.services.daily_trading_state import DailyTradingStateService
from app.services.order_ledger_service import OrderLedgerService
from app.services.reconciliation_queue_service import ReconciliationQueueService
from app.services.safety_ledger import SafetyLedgerService
from app.services.action_approval_service import ActionApprovalService

router = APIRouter(prefix="/v1/workspaces/{workspace_id}/bots/{bot_id}", tags=["live-trading"])


def _get_registry(request: Request):
    return getattr(request.app.state, "registry", None)


@router.get("/timeline")
async def get_timeline(
    workspace_id: str,
    bot_id: str,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc = SafetyLedgerService(db)
    return await svc.timeline(bot_id, limit)


@router.get("/operations-dashboard")
async def get_operations_dashboard(
    workspace_id: str,
    bot_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc = SafetyLedgerService(db)
    exps = ExperimentRegistryService(db)
    registry = _get_registry(request)
    runtime = registry.get(bot_id) if registry is not None else None

    runtime_snapshot = None
    if runtime is not None and hasattr(runtime, "get_snapshot"):
        try:
            runtime_snapshot = await runtime.get_snapshot()
        except Exception as exc:
            runtime_snapshot = {"status": "error", "error_message": str(exc)}

    receipts = await svc.list_execution_receipts(bot_id, 10)
    transitions = await svc.list_order_state_transitions(bot_id, 10)
    account_snapshots = await svc.list_broker_account_snapshots(bot_id, 10)
    reconciliation_runs = await svc.list_reconciliation_runs(bot_id, 10)
    incidents = await svc.list_incidents(bot_id, 20)
    daily_state = await svc.get_daily_state(bot_id)
    experiments = await exps.list_experiments(bot_id, 10)

    return {
        "bot_id": bot_id,
        "workspace_id": workspace_id,
        "runtime": runtime_snapshot,
        "daily_state": daily_state,
        "open_incidents": [i for i in incidents if str(getattr(i, "status", "")).lower() != "resolved"],
        "latest_reconciliation": reconciliation_runs[0] if reconciliation_runs else None,
        "latest_receipts": receipts,
        "latest_transitions": transitions,
        "latest_account_snapshot": account_snapshots[0] if account_snapshots else None,
        "latest_experiment": experiments[0] if experiments else None,
        "experiments": experiments,
    }


@router.get("/decision-ledger")
async def get_decisions(
    workspace_id: str,
    bot_id: str,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc = SafetyLedgerService(db)
    timeline = await svc.timeline(bot_id, limit)
    return timeline["decisions"]


@router.get("/gate-events")
async def get_gate_events(
    workspace_id: str,
    bot_id: str,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc = SafetyLedgerService(db)
    timeline = await svc.timeline(bot_id, limit)
    return timeline["gate_events"]


@router.get("/order-state-transitions")
async def get_order_state_transitions(
    workspace_id: str,
    bot_id: str,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc = SafetyLedgerService(db)
    return await svc.list_order_state_transitions(bot_id, limit)


@router.get("/execution-receipts")
async def get_execution_receipts(
    workspace_id: str,
    bot_id: str,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc = SafetyLedgerService(db)
    return await svc.list_execution_receipts(bot_id, limit)


@router.get("/account-snapshots")
async def get_account_snapshots(
    workspace_id: str,
    bot_id: str,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc = SafetyLedgerService(db)
    return await svc.list_broker_account_snapshots(bot_id, limit)


@router.get("/daily-state")
async def get_daily_state(
    workspace_id: str,
    bot_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc = SafetyLedgerService(db)
    row = await svc.get_daily_state(bot_id)
    if row is None:
        return {
            "bot_instance_id": bot_id,
            "locked": False,
            "daily_profit_amount": 0.0,
            "daily_loss_pct": 0.0,
            "consecutive_losses": 0,
            "trades_count": 0,
            "trading_day": None,
        }
    return row


@router.get("/incidents")
async def get_incidents(
    workspace_id: str,
    bot_id: str,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc = SafetyLedgerService(db)
    return await svc.list_incidents(bot_id, limit)


@router.get("/reconciliation-runs")
async def get_reconciliation_runs(
    workspace_id: str,
    bot_id: str,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc = SafetyLedgerService(db)
    return await svc.list_reconciliation_runs(bot_id, limit)


@router.get("/reconciliation/queue-items")
async def list_reconciliation_queue_items(
    workspace_id: str,
    bot_id: str,
    statuses: str = "failed_needs_operator,dead_letter,pending,retry",
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    allowed = {
        "pending",
        "retry",
        "in_progress",
        "resolved",
        "cancelled",
        "failed_needs_operator",
        "dead_letter",
    }
    requested = [str(s).strip().lower() for s in str(statuses or "").split(",") if str(s).strip()]
    filtered = [s for s in requested if s in allowed]
    if not filtered:
        filtered = ["failed_needs_operator", "dead_letter", "pending", "retry"]

    rows = (
        (
            await db.execute(
                select(ReconciliationQueueItem)
                .where(
                    ReconciliationQueueItem.bot_instance_id == bot_id,
                    ReconciliationQueueItem.status.in_(filtered),
                )
                .order_by(ReconciliationQueueItem.updated_at.desc())
                .limit(max(1, min(int(limit), 500)))
            )
        )
        .scalars()
        .all()
    )
    return rows


@router.post("/reconciliation/{queue_item_id}/resolve")
async def manual_resolve_reconciliation_item(
    workspace_id: str,
    bot_id: str,
    queue_item_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Operator/admin manual resolution for dead-letter or ambiguous unknown orders."""
    if not bool(getattr(current_user, "is_superuser", False)):
        raise HTTPException(status_code=403, detail="Admin permission required")

    approval_id_raw = (payload or {}).get("approval_id")
    try:
        approval_id = int(approval_id_raw) if approval_id_raw is not None else None
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="invalid_approval_id")
    approval_svc = ActionApprovalService(db)
    await approval_svc.validate_and_consume_approval(
        approval_id=approval_id,
        workspace_id=workspace_id,
        action_type="retry_unknown_order",
        bot_instance_id=bot_id,
        actor_user_id=str(getattr(current_user, "id", "") or "") or None,
    )

    outcome = str((payload or {}).get("outcome") or "").lower().strip()
    if outcome not in {"filled", "rejected"}:
        raise HTTPException(status_code=400, detail="outcome must be filled|rejected")

    row = (
        (
            await db.execute(
                select(ReconciliationQueueItem)
                .where(
                    ReconciliationQueueItem.id == queue_item_id,
                    ReconciliationQueueItem.bot_instance_id == bot_id,
                )
                .limit(1)
            )
        )
        .scalar_one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="reconciliation_queue_item_not_found")

    proof_payload = dict((payload or {}).get("broker_proof") or {})
    if not proof_payload:
        raise HTTPException(status_code=400, detail="broker_proof_required")
    provider_name = str(proof_payload.get("provider") or "").strip()
    evidence_ref = str(proof_payload.get("evidence_ref") or "").strip()
    observed_at = str(proof_payload.get("observed_at") or "").strip()
    payload_hash = str(proof_payload.get("payload_hash") or proof_payload.get("raw_response_hash") or "").strip()
    if not provider_name or not evidence_ref or not observed_at:
        raise HTTPException(
            status_code=400,
            detail="broker_proof_invalid_missing_fields:provider|evidence_ref|observed_at",
        )
    if not payload_hash:
        raise HTTPException(status_code=400, detail="broker_proof_missing_payload_hash")
    if outcome == "filled":
        has_fill_identity = any(
            str(proof_payload.get(k) or "").strip()
            for k in ("broker_order_id", "broker_deal_id", "broker_position_id")
        )
        if not has_fill_identity:
            raise HTTPException(
                status_code=400,
                detail="broker_proof_missing_fill_identity",
            )

    ledger = OrderLedgerService(db)
    event_type = "order_filled" if outcome == "filled" else "order_rejected"
    await ledger.record_lifecycle_event(
        bot_instance_id=str(bot_id),
        event_type=event_type,
        idempotency_key=str(row.idempotency_key),
        broker=provider_name or "manual_reconcile",
        payload={
            **proof_payload,
            "manual_resolution": True,
            "resolved_by": str(getattr(current_user, "id", "") or ""),
            "workspace_id": str(workspace_id),
            "signal_id": str(row.signal_id or ""),
        },
    )
    queue_svc = ReconciliationQueueService(db)
    await queue_svc.mark_resolved(str(bot_id), str(row.idempotency_key))

    db.add(
        AuditLog(
            user_id=str(getattr(current_user, "id", "") or ""),
            action="manual_reconciliation_resolve",
            resource_type="reconciliation_queue_item",
            resource_id=str(queue_item_id),
            details={
                "workspace_id": workspace_id,
                "bot_id": bot_id,
                "idempotency_key": str(row.idempotency_key),
                "outcome": outcome,
                "broker_proof": proof_payload,
            },
        )
    )
    await db.commit()
    return {
        "status": "resolved",
        "queue_item_id": queue_item_id,
        "idempotency_key": str(row.idempotency_key),
        "outcome": outcome,
    }


@router.get("/reconciliation/{queue_item_id}/attempt-events")
async def list_reconciliation_attempt_events(
    workspace_id: str,
    bot_id: str,
    queue_item_id: int,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    queue_row = (
        (
            await db.execute(
                select(ReconciliationQueueItem)
                .where(
                    ReconciliationQueueItem.id == queue_item_id,
                    ReconciliationQueueItem.bot_instance_id == bot_id,
                )
                .limit(1)
            )
        )
        .scalar_one_or_none()
    )
    if queue_row is None:
        raise HTTPException(status_code=404, detail="reconciliation_queue_item_not_found")

    rows = (
        (
            await db.execute(
                select(ReconciliationAttemptEvent)
                .where(
                    ReconciliationAttemptEvent.queue_item_id == queue_item_id,
                    ReconciliationAttemptEvent.bot_instance_id == bot_id,
                )
                .order_by(ReconciliationAttemptEvent.created_at.desc())
                .limit(max(1, min(int(limit), 500)))
            )
        )
        .scalars()
        .all()
    )
    return rows


@router.post("/reconcile-now")
async def reconcile_now(
    workspace_id: str,
    bot_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    registry = _get_registry(request)
    if registry is None:
        raise HTTPException(status_code=503, detail="Runtime registry unavailable")
    runtime = registry.get(bot_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="Runtime not found")
    try:
        if hasattr(runtime, "reconcile_now"):
            return await runtime.reconcile_now()
        worker = getattr(runtime, "_reconciliation_worker", None)
        if worker is None:
            raise RuntimeError("reconciliation_worker_not_running")
        result = await worker.run_once()
        return result.to_dict() if hasattr(result, "to_dict") else dict(result)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/incidents/{incident_id}/resolve")
async def resolve_incident(
    workspace_id: str,
    bot_id: str,
    incident_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc = SafetyLedgerService(db)
    row = await svc.resolve_incident(incident_id)
    if row is None or row.bot_instance_id != bot_id:
        raise HTTPException(status_code=404, detail="Incident not found")
    return {"status": row.status, "incident_id": row.id}


@router.post("/daily-state/reset-lock")
async def reset_daily_state_lock(
    workspace_id: str,
    bot_id: str,
    request: Request,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not bool(getattr(current_user, "is_superuser", False)):
        raise HTTPException(status_code=403, detail="Admin permission required")
    approval_id_raw = (payload or {}).get("approval_id")
    try:
        approval_id = int(approval_id_raw) if approval_id_raw is not None else None
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="invalid_approval_id")
    approval_svc = ActionApprovalService(db)
    await approval_svc.validate_and_consume_approval(
        approval_id=approval_id,
        workspace_id=workspace_id,
        action_type="unlock_daily_lock",
        bot_instance_id=bot_id,
        actor_user_id=str(getattr(current_user, "id", "") or "") or None,
    )
    reason = str((payload or {}).get("reason") or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="Reset reason required")
    scope = str((payload or {}).get("scope") or "bot").strip().lower()
    if scope not in {"bot", "portfolio"}:
        raise HTTPException(status_code=400, detail="scope must be bot|portfolio")
    acknowledged = bool((payload or {}).get("acknowledge_operator_action", False))

    related_bot_ids = [bot_id]
    if scope == "portfolio":
        bot_ids = (
            (
                await db.execute(
                    select(BotInstance.id).where(BotInstance.workspace_id == workspace_id)
                )
            )
            .scalars()
            .all()
        )
        related_bot_ids = [str(b) for b in bot_ids]

    svc = SafetyLedgerService(db)
    open_critical = (
        (
            await db.execute(
                select(TradingIncident)
                .where(
                    TradingIncident.bot_instance_id.in_(related_bot_ids),
                    TradingIncident.status != "resolved",
                    TradingIncident.severity == "critical",
                    TradingIncident.incident_type.in_(
                        [
                            "daily_lock_close_all_postcondition_failed",
                            "daily_lock_controller_failure",
                        ]
                    ),
                )
                .limit(1)
            )
        )
        .scalar_one_or_none()
    )
    if open_critical is not None and not acknowledged:
        raise HTTPException(
            status_code=409,
            detail="operator_ack_required_for_open_critical_daily_lock_incident",
        )

    daily = DailyTradingStateService(db)
    if scope == "portfolio":
        states = await daily.reset_workspace_locks(workspace_id)
        if not states:
            raise HTTPException(status_code=404, detail="No bots found for workspace")
    else:
        state = await svc.reset_daily_lock(bot_id)
        if state is None:
            raise HTTPException(status_code=404, detail="Daily state not found")
        states = [state]

    db.add(
        AuditLog(
            user_id=str(getattr(current_user, "id", "") or ""),
            action="daily_state_reset_lock",
            resource_type="workspace" if scope == "portfolio" else "bot_instance",
            resource_id=workspace_id if scope == "portfolio" else bot_id,
            details={
                "reason": reason,
                "workspace_id": workspace_id,
                "scope": scope,
                "acknowledge_operator_action": acknowledged,
                "bot_ids": related_bot_ids,
            },
        )
    )
    await db.commit()
    registry = _get_registry(request)
    for target_bot_id in related_bot_ids:
        if registry is None or registry.get(target_bot_id) is None:
            continue
        runtime = registry.get(target_bot_id)
        state_obj = getattr(runtime, "state", None)
        metadata = getattr(state_obj, "metadata", {})
        if isinstance(metadata, dict):
            metadata["kill_switch"] = False
            metadata["new_orders_paused"] = False
    return {
        "bot_instance_id": bot_id,
        "scope": scope,
        "affected_bots": [str(getattr(s, "bot_instance_id", "")) for s in states],
        "locked": any(bool(getattr(s, "locked", False)) for s in states),
        "lock_reason": next((getattr(s, "lock_reason", None) for s in states if getattr(s, "lock_reason", None)), None),
    }


@router.post("/kill-switch")
async def set_kill_switch(
    workspace_id: str,
    bot_id: str,
    request: Request,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not bool(getattr(current_user, "is_superuser", False)):
        raise HTTPException(status_code=403, detail="Admin permission required")
    reason = str((payload or {}).get("reason") or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="Kill-switch reason required")
    registry = _get_registry(request)
    runtime = registry.get(bot_id) if registry is not None else None
    if runtime is None:
        raise HTTPException(status_code=404, detail="Runtime not found")
    state_obj = getattr(runtime, "state", None)
    metadata = getattr(state_obj, "metadata", {})
    if not isinstance(metadata, dict):
        raise HTTPException(status_code=500, detail="Runtime metadata unavailable")
    metadata["kill_switch"] = True
    if state_obj is not None:
        state_obj.error_message = "kill_switch_enabled_by_operator"
    db.add(
        AuditLog(
            user_id=str(getattr(current_user, "id", "") or ""),
            action="kill_switch_enable",
            resource_type="bot_instance",
            resource_id=str(bot_id),
            details={
                "workspace_id": workspace_id,
                "reason": reason,
            },
        )
    )
    await db.commit()
    return {"bot_instance_id": bot_id, "kill_switch": True}


@router.post("/reset-kill-switch")
async def reset_kill_switch(
    workspace_id: str,
    bot_id: str,
    request: Request,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not bool(getattr(current_user, "is_superuser", False)):
        raise HTTPException(status_code=403, detail="Admin permission required")
    approval_id_raw = (payload or {}).get("approval_id")
    try:
        approval_id = int(approval_id_raw) if approval_id_raw is not None else None
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="invalid_approval_id")
    approval_svc = ActionApprovalService(db)
    await approval_svc.validate_and_consume_approval(
        approval_id=approval_id,
        workspace_id=workspace_id,
        action_type="disable_kill_switch",
        bot_instance_id=bot_id,
        actor_user_id=str(getattr(current_user, "id", "") or "") or None,
    )
    reason = str((payload or {}).get("reason") or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="Kill-switch reset reason required")
    registry = _get_registry(request)
    runtime = registry.get(bot_id) if registry is not None else None
    if runtime is None:
        raise HTTPException(status_code=404, detail="Runtime not found")
    state_obj = getattr(runtime, "state", None)
    metadata = getattr(state_obj, "metadata", {})
    if not isinstance(metadata, dict):
        raise HTTPException(status_code=500, detail="Runtime metadata unavailable")
    metadata["kill_switch"] = False
    db.add(
        AuditLog(
            user_id=str(getattr(current_user, "id", "") or ""),
            action="kill_switch_reset",
            resource_type="bot_instance",
            resource_id=str(bot_id),
            details={
                "workspace_id": workspace_id,
                "reason": reason,
            },
        )
    )
    await db.commit()
    return {"bot_instance_id": bot_id, "kill_switch": False}
