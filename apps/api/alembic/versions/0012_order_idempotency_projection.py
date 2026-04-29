"""Add idempotency_key and source_attempt_id to orders table.

This separates the projection lookup key (idempotency_key) from the real
broker order id (broker_order_id).  broker_order_id is now nullable so that
an order row can be created before the broker assigns an id.

Revision ID: 0012_order_idempotency_projection
Revises: 0011_account_snapshots_and_experiment_registry
Create Date: 2026-04-29
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0012_order_idempotency_projection"
down_revision = "0011_account_snapshots_and_experiment_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("orders") as batch_op:
        # broker_order_id is now optional (not assigned until broker confirms)
        batch_op.alter_column("broker_order_id", existing_type=sa.String(100), nullable=True)
        # new projection key
        batch_op.add_column(sa.Column("idempotency_key", sa.String(256), nullable=True))
        batch_op.add_column(sa.Column("source_attempt_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_orders_idempotency_key", ["idempotency_key"])
        batch_op.create_unique_constraint(
            "uq_orders_bot_idempotency", ["bot_instance_id", "idempotency_key"]
        )


def downgrade() -> None:
    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_constraint("uq_orders_bot_idempotency", type_="unique")
        batch_op.drop_index("ix_orders_idempotency_key")
        batch_op.drop_column("source_attempt_id")
        batch_op.drop_column("idempotency_key")
        batch_op.alter_column("broker_order_id", existing_type=sa.String(100), nullable=False)
