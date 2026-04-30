"""Add submit_outbox_events append-only table for broker submit timeline.

Revision ID: 0021_submit_outbox_events
Revises: 0020_worker_heartbeats
Create Date: 2026-04-30
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0021_submit_outbox_events"
down_revision = "0020_worker_heartbeats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "submit_outbox_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("bot_instance_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=256), nullable=False),
        sa.Column("phase", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=128), nullable=True),
        sa.Column("payload_hash", sa.String(length=128), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("phase_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_submit_outbox_events_bot_instance_id",
        "submit_outbox_events",
        ["bot_instance_id"],
        unique=False,
    )
    op.create_index(
        "ix_submit_outbox_events_bot_idem",
        "submit_outbox_events",
        ["bot_instance_id", "idempotency_key"],
        unique=False,
    )
    op.create_index(
        "ix_submit_outbox_events_phase_created",
        "submit_outbox_events",
        ["phase", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_submit_outbox_events_phase_created", table_name="submit_outbox_events")
    op.drop_index("ix_submit_outbox_events_bot_idem", table_name="submit_outbox_events")
    op.drop_index("ix_submit_outbox_events_bot_instance_id", table_name="submit_outbox_events")
    op.drop_table("submit_outbox_events")
