"""Broker connections router."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.credentials_crypto import (
    decrypt_credentials,
    encrypt_credentials,
    redact_credentials,
    rotate_credentials_encryption,
)
from app.core.db import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.permissions import require_workspace_role
from app.models import BrokerConnection, User
from app.schemas import BrokerConnectionCreate, BrokerConnectionOut, BrokerConnectionUpdate
from app.services.action_approval_service import ActionApprovalService

router = APIRouter(
    prefix="/v1/workspaces/{workspace_id}/broker-connections",
    tags=["broker-connections"],
)
logger = logging.getLogger(__name__)


@router.post("", response_model=BrokerConnectionOut, status_code=status.HTTP_201_CREATED)
async def create_connection(
    workspace_id: str,
    body: BrokerConnectionCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _member=Depends(require_workspace_role("admin")),
):
    conn = BrokerConnection(
        workspace_id=workspace_id,
        name=body.name,
        broker_type=body.broker_type,
        credential_scope=body.credential_scope,
        credentials_encrypted=encrypt_credentials(body.credentials),
    )
    db.add(conn)
    await db.flush()
    return conn


@router.get("", response_model=list[BrokerConnectionOut])
async def list_connections(
    workspace_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _member=Depends(require_workspace_role("viewer")),
):
    result = await db.execute(
        select(BrokerConnection).where(BrokerConnection.workspace_id == workspace_id)
    )
    return result.scalars().all()


@router.get("/{conn_id}", response_model=BrokerConnectionOut)
async def get_connection(
    workspace_id: str,
    conn_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _member=Depends(require_workspace_role("viewer")),
):
    result = await db.execute(
        select(BrokerConnection).where(
            BrokerConnection.id == conn_id,
            BrokerConnection.workspace_id == workspace_id,
        )
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    return conn


@router.patch("/{conn_id}", response_model=BrokerConnectionOut)
async def update_connection(
    workspace_id: str,
    conn_id: str,
    body: BrokerConnectionUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _member=Depends(require_workspace_role("admin")),
):
    result = await db.execute(
        select(BrokerConnection).where(
            BrokerConnection.id == conn_id,
            BrokerConnection.workspace_id == workspace_id,
        )
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    updates = body.model_dump(exclude_none=True)
    if "name" in updates:
        conn.name = updates["name"]
    if "credential_scope" in updates:
        conn.credential_scope = updates["credential_scope"]
    if "is_active" in updates:
        conn.is_active = updates["is_active"]
    if "credentials" in updates:
        credentials_update = dict(updates["credentials"] or {})
        approval_id_raw = credentials_update.pop("approval_id", None)
        try:
            approval_id = int(approval_id_raw) if approval_id_raw is not None else None
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="invalid_approval_id")
        approval_svc = ActionApprovalService(db)
        await approval_svc.validate_and_consume_approval(
            approval_id=approval_id,
            workspace_id=workspace_id,
            action_type="change_provider_credential",
            bot_instance_id=None,
            actor_user_id=str(getattr(current_user, "id", "") or "") or None,
        )
        conn.credentials_encrypted = encrypt_credentials(credentials_update)
    return conn


@router.post("/{conn_id}/rotate-credentials", response_model=BrokerConnectionOut)
async def rotate_connection_credentials(
    workspace_id: str,
    conn_id: str,
    payload: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _member=Depends(require_workspace_role("admin")),
):
    result = await db.execute(
        select(BrokerConnection).where(
            BrokerConnection.id == conn_id,
            BrokerConnection.workspace_id == workspace_id,
        )
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")

    approval_id_raw = (payload or {}).get("approval_id")
    try:
        approval_id = int(approval_id_raw) if approval_id_raw is not None else None
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="invalid_approval_id")
    approval_svc = ActionApprovalService(db)
    await approval_svc.validate_and_consume_approval(
        approval_id=approval_id,
        workspace_id=workspace_id,
        action_type="change_provider_credential",
        bot_instance_id=None,
        actor_user_id=str(getattr(current_user, "id", "") or "") or None,
    )

    conn.credentials_encrypted = rotate_credentials_encryption(conn.credentials_encrypted)
    return conn


@router.delete("/{conn_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(
    workspace_id: str,
    conn_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _member=Depends(require_workspace_role("admin")),
):
    result = await db.execute(
        select(BrokerConnection).where(
            BrokerConnection.id == conn_id,
            BrokerConnection.workspace_id == workspace_id,
        )
    )
    conn = result.scalar_one_or_none()
    if conn:
        await db.delete(conn)


@router.post("/{conn_id}/test")
async def test_connection(
    workspace_id: str,
    conn_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _member=Depends(require_workspace_role("admin")),
):
    result = await db.execute(
        select(BrokerConnection).where(
            BrokerConnection.id == conn_id,
            BrokerConnection.workspace_id == workspace_id,
        )
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    # Try a test connect
    try:
        from execution_service.providers import get_provider
        credentials = decrypt_credentials(conn.credentials_encrypted)
        provider = get_provider(conn.broker_type, **credentials)
        await provider.connect()
        await provider.disconnect()
        return {"status": "ok", "message": "Connection successful"}
    except Exception:
        logger.warning(
            "Connection test failed for broker connection %s (type=%s, credentials=%s)",
            conn_id,
            conn.broker_type,
            redact_credentials(decrypt_credentials(conn.credentials_encrypted)),
        )
        return {"status": "error", "message": "Connection test failed"}
