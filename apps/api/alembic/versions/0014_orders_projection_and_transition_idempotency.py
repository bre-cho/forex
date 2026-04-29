"""Expand orders projection and enforce transition idempotency.

Revision ID: 0014_orders_projection_and_transition_idempotency
Revises: 0013_reconciliation_queue
Create Date: 2026-04-29
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0014_orders_projection_and_transition_idempotency"
down_revision = "0013_reconciliation_queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("current_state", sa.String(length=64), nullable=True))
    op.add_column("orders", sa.Column("submit_status", sa.String(length=32), nullable=True))
    op.add_column("orders", sa.Column("fill_status", sa.String(length=32), nullable=True))
    op.add_column("orders", sa.Column("broker_position_id", sa.String(length=128), nullable=True))
    op.add_column("orders", sa.Column("broker_deal_id", sa.String(length=128), nullable=True))
    op.add_column("orders", sa.Column("avg_fill_price", sa.Float(), nullable=True))
    op.add_column("orders", sa.Column("filled_volume", sa.Float(), nullable=False, server_default="0"))
    op.add_column("orders", sa.Column("reconciliation_status", sa.String(length=32), nullable=True))
    op.add_column("orders", sa.Column("last_transition_at", sa.DateTime(timezone=True), nullable=True))

    op.create_unique_constraint(
        "uq_order_transition_event_key",
        "order_state_transitions",
        ["bot_instance_id", "idempotency_key", "event_type", "to_state"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_order_transition_event_key", "order_state_transitions", type_="unique")

    op.drop_column("orders", "last_transition_at")
    op.drop_column("orders", "reconciliation_status")
    op.drop_column("orders", "filled_volume")
    op.drop_column("orders", "avg_fill_price")
    op.drop_column("orders", "broker_deal_id")
    op.drop_column("orders", "broker_position_id")
    op.drop_column("orders", "fill_status")
    op.drop_column("orders", "submit_status")
    op.drop_column("orders", "current_state")
