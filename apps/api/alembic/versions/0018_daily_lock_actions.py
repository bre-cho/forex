"""Add daily_lock_actions table for exactly-once lock action log (P0.3).

Revision ID: 0018_daily_lock_actions
Revises: 0017_reconciliation_queue_lease
Create Date: 2026-04-29
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0018_daily_lock_actions"
down_revision = "0017_reconciliation_queue_lease"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "daily_lock_actions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("bot_instance_id", sa.String(64), nullable=False, index=True),
        sa.Column("trading_day", sa.Date(), nullable=False),
        sa.Column("lock_reason", sa.String(128), nullable=True),
        sa.Column("lock_action", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("positions_before", sa.Integer(), nullable=True),
        sa.Column("positions_after", sa.Integer(), nullable=True),
        sa.Column("action_detail", sa.JSON(), nullable=True),
        sa.Column("action_hash", sa.String(128), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "bot_instance_id", "trading_day", "lock_action",
            name="uq_daily_lock_action_bot_day_action",
        ),
    )


def downgrade() -> None:
    op.drop_table("daily_lock_actions")
