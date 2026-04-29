"""Add gate_context_hash to broker order attempts.

Revision ID: 0015_broker_attempt_gate_context_hash
Revises: 0014_orders_projection_and_transition_idempotency
Create Date: 2026-04-29
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0015_broker_attempt_gate_context_hash"
down_revision = "0014_orders_projection_and_transition_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "broker_order_attempts",
        sa.Column("gate_context_hash", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("broker_order_attempts", "gate_context_hash")
