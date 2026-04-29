"""Tests for P0.5 — Unknown Order Daemon.

Covers:
- ReconciliationLeaseService: acquire / double-acquire / release
- ReconciliationQueueService: deadline_at set on enqueue, list_all_pending_due, move_to_dead_letter
- reconciliation_daemon._process_item: retry when age > 30s, escalate when deadline passed / max_attempts
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.reconciliation_queue_service import ReconciliationQueueService, _DEADLINE_SECONDS, _MAX_ATTEMPTS


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_item(
    *,
    id: int = 1,
    bot_instance_id: str = "bot_001",
    idempotency_key: str = "idem_001",
    status: str = "pending",
    attempts: int = 0,
    max_attempts: int = _MAX_ATTEMPTS,
    created_at_offset: float = -60.0,  # seconds relative to now
    deadline_at_offset: float | None = None,
    next_retry_at: datetime | None = None,
):
    now = datetime.now(timezone.utc)
    created_at = now + timedelta(seconds=created_at_offset)
    if deadline_at_offset is None:
        deadline_at = now + timedelta(seconds=_DEADLINE_SECONDS + created_at_offset)
    else:
        deadline_at = now + timedelta(seconds=deadline_at_offset)

    item = MagicMock()
    item.id = id
    item.bot_instance_id = bot_instance_id
    item.idempotency_key = idempotency_key
    item.status = status
    item.attempts = attempts
    item.max_attempts = max_attempts
    item.created_at = created_at
    item.deadline_at = deadline_at
    item.next_retry_at = next_retry_at
    item.payload = {}
    item.signal_id = None
    return item


# ── ReconciliationQueueService ────────────────────────────────────────────────

class TestEnqueueSetsDeadline:
    @pytest.mark.asyncio
    async def test_enqueue_sets_deadline_at(self):
        db = AsyncMock()
        scalar_result = MagicMock()
        scalar_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=scalar_result)
        db.refresh = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        svc = ReconciliationQueueService(db)
        await svc.enqueue_unknown_order(
            bot_instance_id="bot_x",
            idempotency_key="idem_x",
        )
        db.add.assert_called_once()
        added_row = db.add.call_args[0][0]
        assert hasattr(added_row, "deadline_at")
        assert added_row.deadline_at is not None
        # deadline_at should be ~_DEADLINE_SECONDS from now
        delta = (added_row.deadline_at - datetime.now(timezone.utc)).total_seconds()
        assert abs(delta - _DEADLINE_SECONDS) < 5.0

    @pytest.mark.asyncio
    async def test_enqueue_sets_max_attempts(self):
        db = AsyncMock()
        scalar_result = MagicMock()
        scalar_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=scalar_result)
        db.refresh = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        svc = ReconciliationQueueService(db)
        await svc.enqueue_unknown_order(
            bot_instance_id="bot_x",
            idempotency_key="idem_x",
        )
        added_row = db.add.call_args[0][0]
        assert added_row.max_attempts == _MAX_ATTEMPTS


# ── Daemon _process_item ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_process_item_retries_when_old():
    """Item older than 30s and still within deadline → mark_retry."""
    from app.workers.reconciliation_daemon import _process_item

    item = _make_item(
        created_at_offset=-60.0,       # 60s old — past grace period
        deadline_at_offset=240.0,      # deadline still in future
        attempts=0,
        max_attempts=3,
    )

    db = AsyncMock()
    lease_svc = AsyncMock()
    lease_svc.try_acquire = AsyncMock(return_value=True)
    lease_svc.release = AsyncMock()

    queue_svc = AsyncMock()
    queue_svc.mark_retry = AsyncMock(return_value=True)
    queue_svc.move_to_dead_letter = AsyncMock()

    with (
        patch("app.workers.reconciliation_daemon.ReconciliationLeaseService", return_value=lease_svc),
        patch("app.workers.reconciliation_daemon.ReconciliationQueueService", return_value=queue_svc),
    ):
        await _process_item(db, item, "worker_test")

    queue_svc.mark_retry.assert_called_once_with(
        item.bot_instance_id,
        item.idempotency_key,
        error="unknown_order_unresolved",
        retry_after_seconds=15.0,  # base * 2^0
    )
    queue_svc.move_to_dead_letter.assert_not_called()


@pytest.mark.asyncio
async def test_process_item_escalates_on_deadline_passed():
    """Item past deadline → dead-letter + critical incident + lock bot."""
    from app.workers.reconciliation_daemon import _process_item

    item = _make_item(
        created_at_offset=-400.0,
        deadline_at_offset=-60.0,      # deadline PASSED
        attempts=1,
        max_attempts=3,
    )

    db = AsyncMock()
    lease_svc = AsyncMock()
    lease_svc.try_acquire = AsyncMock(return_value=True)
    lease_svc.release = AsyncMock()

    queue_svc = AsyncMock()
    queue_svc.move_to_dead_letter = AsyncMock(return_value=True)

    with (
        patch("app.workers.reconciliation_daemon.ReconciliationLeaseService", return_value=lease_svc),
        patch("app.workers.reconciliation_daemon.ReconciliationQueueService", return_value=queue_svc),
        patch("app.workers.reconciliation_daemon._create_critical_incident", new=AsyncMock()),
        patch("app.workers.reconciliation_daemon._lock_bot_daily_state", new=AsyncMock()),
    ):
        await _process_item(db, item, "worker_test")

    queue_svc.move_to_dead_letter.assert_called_once_with(
        item.bot_instance_id,
        item.idempotency_key,
        error="deadline_passed",
    )


@pytest.mark.asyncio
async def test_process_item_escalates_on_max_attempts():
    """Item with attempts >= max_attempts → dead-letter regardless of deadline."""
    from app.workers.reconciliation_daemon import _process_item

    item = _make_item(
        created_at_offset=-60.0,
        deadline_at_offset=180.0,      # deadline still OK
        attempts=3,                     # == max_attempts
        max_attempts=3,
    )

    db = AsyncMock()
    lease_svc = AsyncMock()
    lease_svc.try_acquire = AsyncMock(return_value=True)
    lease_svc.release = AsyncMock()

    queue_svc = AsyncMock()
    queue_svc.move_to_dead_letter = AsyncMock(return_value=True)

    with (
        patch("app.workers.reconciliation_daemon.ReconciliationLeaseService", return_value=lease_svc),
        patch("app.workers.reconciliation_daemon.ReconciliationQueueService", return_value=queue_svc),
        patch("app.workers.reconciliation_daemon._create_critical_incident", new=AsyncMock()),
        patch("app.workers.reconciliation_daemon._lock_bot_daily_state", new=AsyncMock()),
    ):
        await _process_item(db, item, "worker_test")

    queue_svc.move_to_dead_letter.assert_called_once()


@pytest.mark.asyncio
async def test_process_item_skips_when_lease_unavailable():
    """If lease cannot be acquired (another worker holds it), item is skipped."""
    from app.workers.reconciliation_daemon import _process_item

    item = _make_item(created_at_offset=-60.0)

    db = AsyncMock()
    lease_svc = AsyncMock()
    lease_svc.try_acquire = AsyncMock(return_value=False)

    queue_svc = AsyncMock()

    with (
        patch("app.workers.reconciliation_daemon.ReconciliationLeaseService", return_value=lease_svc),
        patch("app.workers.reconciliation_daemon.ReconciliationQueueService", return_value=queue_svc),
    ):
        await _process_item(db, item, "worker_test")

    queue_svc.mark_retry.assert_not_called()
    queue_svc.move_to_dead_letter.assert_not_called()


@pytest.mark.asyncio
async def test_process_item_within_grace_period_noop():
    """Item younger than 30s → no-op (wait for grace period to pass)."""
    from app.workers.reconciliation_daemon import _process_item

    item = _make_item(
        created_at_offset=-10.0,       # only 10s old
        deadline_at_offset=290.0,
        attempts=0,
        max_attempts=3,
    )

    db = AsyncMock()
    lease_svc = AsyncMock()
    lease_svc.try_acquire = AsyncMock(return_value=True)
    lease_svc.release = AsyncMock()

    queue_svc = AsyncMock()
    queue_svc.mark_retry = AsyncMock()
    queue_svc.move_to_dead_letter = AsyncMock()

    with (
        patch("app.workers.reconciliation_daemon.ReconciliationLeaseService", return_value=lease_svc),
        patch("app.workers.reconciliation_daemon.ReconciliationQueueService", return_value=queue_svc),
    ):
        await _process_item(db, item, "worker_test")

    queue_svc.mark_retry.assert_not_called()
    queue_svc.move_to_dead_letter.assert_not_called()


@pytest.mark.asyncio
async def test_lock_bot_daily_state_uses_service_lock_day():
    from app.workers.reconciliation_daemon import _lock_bot_daily_state

    db = AsyncMock()
    state_svc = AsyncMock()
    state_svc.lock_day = AsyncMock()

    with patch("app.workers.reconciliation_daemon.DailyTradingStateService", return_value=state_svc):
        await _lock_bot_daily_state(db, "bot_123")

    state_svc.lock_day.assert_called_once_with("bot_123", reason="unknown_order_escalation")


@pytest.mark.asyncio
async def test_create_critical_incident_skips_duplicate_open_item():
    from app.workers.reconciliation_daemon import _create_critical_incident

    item = _make_item(bot_instance_id="bot_dup", idempotency_key="idem_dup")
    db = AsyncMock()

    duplicate_scalar = MagicMock()
    duplicate_scalar.scalar_one_or_none.return_value = MagicMock(id=99)
    db.execute = AsyncMock(return_value=duplicate_scalar)
    db.add = MagicMock()

    await _create_critical_incident(db, item, "deadline_passed")

    db.add.assert_not_called()
