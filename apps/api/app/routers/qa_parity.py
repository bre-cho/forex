from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies.auth import get_current_user
from app.models import User

router = APIRouter(prefix="/v1/qa/parity-contract", tags=["qa-parity"])

_ALLOWED_MODES = {"backtest", "paper", "demo", "live"}


def _check_mode(mode: str) -> str:
    normalized = str(mode or "").lower().strip()
    if normalized not in _ALLOWED_MODES:
        raise HTTPException(status_code=400, detail=f"unsupported_mode:{normalized}")
    return normalized


@router.post("/check")
async def check_parity_contract(
    payload: dict,
    current_user: User = Depends(get_current_user),
):
    mode = _check_mode(str(payload.get("mode") or ""))
    envelope = payload.get("payload")
    if not isinstance(envelope, dict):
        raise HTTPException(status_code=400, detail="payload must be an object")

    try:
        from execution_service.parity_contract import validate_order_contract
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"parity_contract_unavailable:{exc}") from exc

    result = validate_order_contract(mode, envelope)
    return {
        "mode": mode,
        "ok": bool(result.ok),
        "reason": str(result.reason),
        "missing": list(result.missing),
    }


@router.post("/audit")
async def audit_parity_contract(
    payload: dict,
    current_user: User = Depends(get_current_user),
):
    modes = payload.get("modes")
    if not isinstance(modes, list) or not modes:
        raise HTTPException(status_code=400, detail="modes must be a non-empty list")
    envelope = payload.get("payload")
    if not isinstance(envelope, dict):
        raise HTTPException(status_code=400, detail="payload must be an object")

    try:
        from execution_service.parity_contract import validate_order_contract
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"parity_contract_unavailable:{exc}") from exc

    results = []
    for mode in modes:
        normalized = _check_mode(str(mode or ""))
        result = validate_order_contract(normalized, envelope)
        results.append(
            {
                "mode": normalized,
                "ok": bool(result.ok),
                "reason": str(result.reason),
                "missing": list(result.missing),
            }
        )

    return {
        "results": results,
        "all_ok": all(bool(item["ok"]) for item in results),
    }
