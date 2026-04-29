"""Create reconciliation queue for UNKNOWN order recovery.

Revision ID: 0013_reconciliation_queue
Revises: 0012_order_idempotency_projection
Create Date: 2026-04-29
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0013_reconciliation_queue"
down_revision = "0012_order_idempotency_projection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reconciliation_queue_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("bot_instance_id", sa.String(length=64), nullable=False),
        sa.Column("signal_id", sa.String(length=128), nullable=True),
        sa.Column("idempotency_key", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("bot_instance_id", "idempotency_key", name="uq_recon_queue_bot_idem"),
    )
    op.create_index("ix_recon_queue_bot", "reconciliation_queue_items", ["bot_instance_id"])
    op.create_index("ix_recon_queue_status", "reconciliation_queue_items", ["status"])


def downgrade() -> None:
    op.drop_index("ix_recon_queue_status", table_name="reconciliation_queue_items")
    op.drop_index("ix_recon_queue_bot", table_name="reconciliation_queue_items")
    op.drop_table("reconciliation_queue_items")
