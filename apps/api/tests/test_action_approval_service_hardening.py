from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.db import Base
from app.models import ActionApprovalRequest
from app.services.action_approval_service import ActionApprovalService


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_validate_and_consume_live_failover_approval_with_binding_ttl_and_digest() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_maker() as db:
        row = ActionApprovalRequest(
            workspace_id="ws-1",
            bot_instance_id="bot-1",
            action_type="live_provider_failover",
            status="approved",
            reason="Failover approval",
            request_payload={
                "primary_provider": "primary",
                "backup_providers": ["backup"],
                "reason_digest": "abc123",
            },
            decided_at=_now_utc(),
        )
        db.add(row)
        await db.flush()

        svc = ActionApprovalService(db)
        consumed = await svc.validate_and_consume_approval(
            approval_id=int(row.id),
            workspace_id="ws-1",
            action_type="live_provider_failover",
            bot_instance_id="bot-1",
            actor_user_id="user-1",
            expected_payload={
                "primary_provider": "primary",
                "backup_providers": ["backup"],
            },
            approval_ttl_seconds=300,
            required_reason_digest="abc123",
        )
        await db.commit()

        assert consumed.status == "consumed"
        assert consumed.consumed_at is not None


@pytest.mark.asyncio
async def test_validate_and_consume_live_failover_approval_rejects_provider_mismatch() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_maker() as db:
        row = ActionApprovalRequest(
            workspace_id="ws-1",
            bot_instance_id="bot-1",
            action_type="live_provider_failover",
            status="approved",
            reason="Failover approval",
            request_payload={
                "primary_provider": "primary",
                "backup_providers": ["backup"],
                "reason_digest": "abc123",
            },
            decided_at=_now_utc(),
        )
        db.add(row)
        await db.flush()

        svc = ActionApprovalService(db)
        with pytest.raises(HTTPException) as exc:
            await svc.validate_and_consume_approval(
                approval_id=int(row.id),
                workspace_id="ws-1",
                action_type="live_provider_failover",
                bot_instance_id="bot-1",
                actor_user_id="user-1",
                expected_payload={
                    "primary_provider": "other-primary",
                    "backup_providers": ["backup"],
                },
                approval_ttl_seconds=300,
                required_reason_digest="abc123",
            )

        assert exc.value.status_code == 403
        assert "approval_payload_mismatch:primary_provider" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_validate_and_consume_live_failover_approval_rejects_ttl_exceeded() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_maker() as db:
        row = ActionApprovalRequest(
            workspace_id="ws-1",
            bot_instance_id="bot-1",
            action_type="live_provider_failover",
            status="approved",
            reason="Failover approval",
            request_payload={
                "primary_provider": "primary",
                "backup_providers": ["backup"],
                "reason_digest": "abc123",
            },
            decided_at=_now_utc() - timedelta(seconds=601),
        )
        db.add(row)
        await db.flush()

        svc = ActionApprovalService(db)
        with pytest.raises(HTTPException) as exc:
            await svc.validate_and_consume_approval(
                approval_id=int(row.id),
                workspace_id="ws-1",
                action_type="live_provider_failover",
                bot_instance_id="bot-1",
                actor_user_id="user-1",
                expected_payload={
                    "primary_provider": "primary",
                    "backup_providers": ["backup"],
                },
                approval_ttl_seconds=300,
                required_reason_digest="abc123",
            )

        assert exc.value.status_code == 403
        assert "approval_ttl_exceeded" in str(exc.value.detail)
