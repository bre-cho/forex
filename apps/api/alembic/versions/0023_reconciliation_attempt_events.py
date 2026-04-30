"""Add reconciliation_attempt_events for broker lookup traceability.

Revision ID: 0023_reconciliation_attempt_events
Revises: 0022_frozen_gate_contexts
Create Date: 2026-04-30
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0023_reconciliation_attempt_events"
down_revision = "0022_frozen_gate_contexts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reconciliation_attempt_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("queue_item_id", sa.Integer(), nullable=True),
        sa.Column("bot_instance_id", sa.String(length=64), nullable=False),
        sa.Column("signal_id", sa.String(length=128), nullable=True),
        sa.Column("idempotency_key", sa.String(length=256), nullable=False),
        sa.Column("worker_id", sa.String(length=128), nullable=True),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=64), nullable=False),
        sa.Column("resolution_code", sa.String(length=64), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("payload_hash", sa.String(length=128), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_reconciliation_attempt_events_queue_item_id",
        "reconciliation_attempt_events",
        ["queue_item_id"],
        unique=False,
    )
    op.create_index(
        "ix_reconciliation_attempt_events_bot_instance_id",
        "reconciliation_attempt_events",
        ["bot_instance_id"],
        unique=False,
    )
    op.create_index(
        "ix_reconciliation_attempt_events_idempotency_key",
        "reconciliation_attempt_events",
        ["idempotency_key"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_reconciliation_attempt_events_idempotency_key", table_name="reconciliation_attempt_events")
    op.drop_index("ix_reconciliation_attempt_events_bot_instance_id", table_name="reconciliation_attempt_events")
    op.drop_index("ix_reconciliation_attempt_events_queue_item_id", table_name="reconciliation_attempt_events")
    op.drop_table("reconciliation_attempt_events")
