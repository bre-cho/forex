"""Add submit_outbox table for before-send/after-send phase tracking.

Revision ID: 0019_submit_outbox
Revises: 0018_daily_lock_actions
Create Date: 2026-04-30
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0019_submit_outbox"
down_revision = "0018_daily_lock_actions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "submit_outbox",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("bot_instance_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=256), nullable=False),
        sa.Column("phase", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=128), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("phase_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("bot_instance_id", "idempotency_key", name="uq_submit_outbox_bot_idem"),
    )
    op.create_index("ix_submit_outbox_bot_instance_id", "submit_outbox", ["bot_instance_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_submit_outbox_bot_instance_id", table_name="submit_outbox")
    op.drop_table("submit_outbox")
