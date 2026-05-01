from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.permissions import require_workspace_role
from app.models import (
    ActionApprovalRequest,
    AuditLog,
    BotInstance,
    DailyLockAction,
    Order,
    TradingIncident,
    User,
)

router = APIRouter(prefix="/v1/workspaces/{workspace_id}/compliance", tags=["compliance"])


def _to_utc_iso(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


@router.get("/export")
async def export_compliance_evidence(
    workspace_id: str,
    export_format: str = Query("json", alias="format"),
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _member=Depends(require_workspace_role("admin")),
):
    fmt = str(export_format or "json").strip().lower()
    if fmt not in {"json", "csv"}:
        raise HTTPException(status_code=400, detail="format must be json|csv")

    now = datetime.now(timezone.utc)
    since = now - timedelta(days=int(days))

    bot_ids = (
        (
            await db.execute(
                select(BotInstance.id).where(BotInstance.workspace_id == workspace_id)
            )
        )
        .scalars()
        .all()
    )
    bot_ids = [str(x) for x in bot_ids]

    audit_rows = (
        (
            await db.execute(
                select(AuditLog)
                .where(AuditLog.created_at >= since)
                .order_by(AuditLog.created_at.desc())
                .limit(5000)
            )
        )
        .scalars()
        .all()
    )
    filtered_audits = [
        row
        for row in audit_rows
        if str((getattr(row, "details", {}) or {}).get("workspace_id") or "") == workspace_id
    ]

    incidents = (
        (
            await db.execute(
                select(TradingIncident)
                .where(
                    TradingIncident.bot_instance_id.in_(bot_ids) if bot_ids else False,
                    TradingIncident.created_at >= since,
                )
                .order_by(TradingIncident.created_at.desc())
                .limit(5000)
            )
        )
        .scalars()
        .all()
    )

    orders = (
        (
            await db.execute(
                select(Order)
                .where(
                    Order.bot_instance_id.in_(bot_ids) if bot_ids else False,
                    Order.created_at >= since,
                )
                .order_by(Order.created_at.desc())
                .limit(5000)
            )
        )
        .scalars()
        .all()
    )

    daily_lock_actions = (
        (
            await db.execute(
                select(DailyLockAction)
                .where(
                    DailyLockAction.bot_instance_id.in_(bot_ids) if bot_ids else False,
                    DailyLockAction.created_at >= since,
                )
                .order_by(DailyLockAction.created_at.desc())
                .limit(5000)
            )
        )
        .scalars()
        .all()
    )

    approvals = (
        (
            await db.execute(
                select(ActionApprovalRequest)
                .where(
                    ActionApprovalRequest.workspace_id == workspace_id,
                    ActionApprovalRequest.created_at >= since,
                )
                .order_by(ActionApprovalRequest.created_at.desc())
                .limit(5000)
            )
        )
        .scalars()
        .all()
    )

    if fmt == "json":
        return JSONResponse(
            {
                "workspace_id": workspace_id,
                "generated_at": _to_utc_iso(now),
                "window_days": days,
                "counts": {
                    "audit_logs": len(filtered_audits),
                    "incidents": len(incidents),
                    "orders": len(orders),
                    "daily_lock_actions": len(daily_lock_actions),
                    "approvals": len(approvals),
                },
                "audit_logs": [
                    {
                        "id": str(row.id),
                        "action": row.action,
                        "resource_type": row.resource_type,
                        "resource_id": row.resource_id,
                        "details": row.details,
                        "created_at": _to_utc_iso(row.created_at),
                    }
                    for row in filtered_audits
                ],
                "incidents": [
                    {
                        "id": int(row.id),
                        "bot_instance_id": str(row.bot_instance_id),
                        "incident_type": row.incident_type,
                        "severity": row.severity,
                        "status": row.status,
                        "created_at": _to_utc_iso(row.created_at),
                    }
                    for row in incidents
                ],
                "orders": [
                    {
                        "id": str(row.id),
                        "bot_instance_id": str(row.bot_instance_id),
                        "symbol": row.symbol,
                        "side": row.side,
                        "status": row.status,
                        "idempotency_key": row.idempotency_key,
                        "created_at": _to_utc_iso(row.created_at),
                    }
                    for row in orders
                ],
                "daily_lock_actions": [
                    {
                        "id": int(row.id),
                        "bot_instance_id": str(row.bot_instance_id),
                        "trading_day": str(row.trading_day),
                        "lock_action": row.lock_action,
                        "status": row.status,
                        "requested_at": _to_utc_iso(row.requested_at),
                        "completed_at": _to_utc_iso(row.completed_at),
                    }
                    for row in daily_lock_actions
                ],
                "approvals": [
                    {
                        "id": int(row.id),
                        "action_type": row.action_type,
                        "status": row.status,
                        "reason": row.reason,
                        "requested_by_user_id": row.requested_by_user_id,
                        "approved_by_user_id": row.approved_by_user_id,
                        "created_at": _to_utc_iso(row.created_at),
                        "decided_at": _to_utc_iso(row.decided_at),
                    }
                    for row in approvals
                ],
            }
        )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["record_type", "record_id", "timestamp", "workspace_id", "bot_instance_id", "action", "status", "detail"])

    for row in filtered_audits:
        writer.writerow([
            "audit_log",
            str(row.id),
            _to_utc_iso(row.created_at),
            workspace_id,
            str((row.details or {}).get("bot_id") or (row.details or {}).get("bot_instance_id") or ""),
            row.action,
            "",
            str(row.details or {}),
        ])
    for row in incidents:
        writer.writerow([
            "incident",
            int(row.id),
            _to_utc_iso(row.created_at),
            workspace_id,
            str(row.bot_instance_id),
            row.incident_type,
            row.status,
            row.severity,
        ])
    for row in orders:
        writer.writerow([
            "order",
            str(row.id),
            _to_utc_iso(row.created_at),
            workspace_id,
            str(row.bot_instance_id),
            f"{row.side} {row.symbol}",
            row.status,
            str(row.idempotency_key or ""),
        ])
    for row in daily_lock_actions:
        writer.writerow([
            "daily_lock_action",
            int(row.id),
            _to_utc_iso(row.created_at),
            workspace_id,
            str(row.bot_instance_id),
            row.lock_action,
            row.status,
            str(row.lock_reason or ""),
        ])
    for row in approvals:
        writer.writerow([
            "approval",
            int(row.id),
            _to_utc_iso(row.created_at),
            workspace_id,
            str(row.bot_instance_id or ""),
            row.action_type,
            row.status,
            str(row.reason or ""),
        ])

    return PlainTextResponse(output.getvalue(), media_type="text/csv")
