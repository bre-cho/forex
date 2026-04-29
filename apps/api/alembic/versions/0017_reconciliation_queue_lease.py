"""Add lease, deadline, max_attempts fields to reconciliation_queue_items.

Revision ID: 0017_reconciliation_queue_lease
Revises: 0016_execution_receipt_contract
Create Date: 2026-05-01
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0017_reconciliation_queue_lease"
down_revision = "0016_execution_receipt_contract"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reconciliation_queue_items",
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
    )
    op.add_column(
        "reconciliation_queue_items",
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "reconciliation_queue_items",
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "reconciliation_queue_items",
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reconciliation_queue_items", "leased_until")
    op.drop_column("reconciliation_queue_items", "lease_owner")
    op.drop_column("reconciliation_queue_items", "deadline_at")
    op.drop_column("reconciliation_queue_items", "max_attempts")
