"""Add worker_heartbeats table for daemon liveness and preflight checks.

Revision ID: 0020_worker_heartbeats
Revises: 0019_submit_outbox
Create Date: 2026-04-30
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0020_worker_heartbeats"
down_revision = "0019_submit_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_heartbeats",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("worker_name", sa.String(length=64), nullable=False),
        sa.Column("worker_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="running"),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("worker_name", "worker_id", name="uq_worker_heartbeat_name_id"),
    )
    op.create_index("ix_worker_heartbeats_worker_name", "worker_heartbeats", ["worker_name"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_worker_heartbeats_worker_name", table_name="worker_heartbeats")
    op.drop_table("worker_heartbeats")
