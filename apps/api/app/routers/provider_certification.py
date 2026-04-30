from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.dependencies.auth import get_current_user
from app.models import User
from app.services.provider_certification_service import ProviderCertificationService


router = APIRouter(
    prefix="/v1/workspaces/{workspace_id}/bots/{bot_id}/provider-certification",
    tags=["provider-certification"],
)


@router.get("/status")
async def get_provider_certification_status(
    workspace_id: str,
    bot_id: str,
    provider: str,
    mode: str = "live",
    account_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc = ProviderCertificationService(db)
    gate = await svc.get_live_gate_status(
        bot_instance_id=bot_id,
        provider=provider,
        account_id=account_id,
    )
    row = gate.get("record")
    if row is None:
        return {
            "bot_instance_id": bot_id,
            "provider": provider,
            "mode": mode,
            "live_certified": False,
            "status": "missing",
            "gate_reason": str(gate.get("reason") or "provider_certification_missing"),
            "record": None,
        }
    return {
        "bot_instance_id": bot_id,
        "provider": provider,
        "mode": mode,
        "live_certified": bool(gate.get("ok", False)),
        "status": "ok" if bool(gate.get("ok", False)) else "blocked",
        "gate_reason": str(gate.get("reason") or "ok"),
        "record": row,
    }


@router.get("/records")
async def list_provider_certification_records(
    workspace_id: str,
    bot_id: str,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc = ProviderCertificationService(db)
    rows = await svc.list_for_bot(bot_instance_id=bot_id, limit=limit)
    return {"bot_instance_id": bot_id, "items": rows}


@router.post("/record")
async def record_provider_certification(
    workspace_id: str,
    bot_id: str,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not bool(getattr(current_user, "is_superuser", False)):
        raise HTTPException(status_code=403, detail="Admin permission required")

    provider = str(payload.get("provider") or "").strip()
    if not provider:
        raise HTTPException(status_code=400, detail="provider is required")

    mode = str(payload.get("mode") or "live").strip().lower()
    checks = payload.get("checks")
    if checks is not None and not isinstance(checks, dict):
        raise HTTPException(status_code=400, detail="checks must be an object")

    evidence = payload.get("evidence")
    if evidence is not None and not isinstance(evidence, dict):
        raise HTTPException(status_code=400, detail="evidence must be an object")
    evidence_obj = dict(evidence or {})

    if mode == "live":
        # Live certification must be backed by smoke-suite evidence, not manual toggles.
        smoke_suite_run_id = str(evidence_obj.get("smoke_suite_run_id") or "").strip()
        artifact_ref = str(evidence_obj.get("artifact_ref") or evidence_obj.get("artifact_uri") or "").strip()
        if not smoke_suite_run_id:
            raise HTTPException(status_code=400, detail="live certification requires evidence.smoke_suite_run_id")
        if not artifact_ref:
            raise HTTPException(status_code=400, detail="live certification requires evidence.artifact_ref")
        if not evidence_obj.get("evidence_hash"):
            evidence_obj["evidence_hash"] = ProviderCertificationService.build_evidence_hash(evidence_obj)

    required_checks = payload.get("required_checks")
    if required_checks is not None and not isinstance(required_checks, list):
        raise HTTPException(status_code=400, detail="required_checks must be an array")

    svc = ProviderCertificationService(db)
    row = await svc.record_certification(
        bot_instance_id=bot_id,
        provider=provider,
        mode=mode,
        account_id=(str(payload.get("account_id")) if payload.get("account_id") else None),
        symbol=(str(payload.get("symbol")) if payload.get("symbol") else None),
        checks=(checks or {}),
        evidence=evidence_obj,
        ttl_seconds=(int(payload.get("ttl_seconds")) if payload.get("ttl_seconds") is not None else None),
        required_checks=required_checks,
        actor_user_id=str(getattr(current_user, "id", "") or "") or None,
    )
    return {
        "status": "recorded",
        "bot_instance_id": bot_id,
        "provider": provider,
        "mode": mode,
        "live_certified": bool(row.live_certified),
        "certification_hash": row.certification_hash,
        "record": row,
    }


@router.post("/revoke")
async def revoke_provider_certification(
    workspace_id: str,
    bot_id: str,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not bool(getattr(current_user, "is_superuser", False)):
        raise HTTPException(status_code=403, detail="Admin permission required")

    provider = str(payload.get("provider") or "").strip()
    if not provider:
        raise HTTPException(status_code=400, detail="provider is required")
    reason = str(payload.get("reason") or "revoked_by_operator").strip() or "revoked_by_operator"
    account_id = str(payload.get("account_id") or "").strip() or None

    svc = ProviderCertificationService(db)
    row = await svc.revoke_latest(
        bot_instance_id=bot_id,
        provider=provider,
        reason=reason,
        actor_user_id=str(getattr(current_user, "id", "") or "") or None,
        account_id=account_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="provider certification record not found")
    return {
        "status": "revoked",
        "bot_instance_id": bot_id,
        "provider": provider,
        "record": row,
    }
