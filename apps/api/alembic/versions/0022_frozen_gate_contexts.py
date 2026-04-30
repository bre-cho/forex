"""Add frozen_gate_contexts and link frozen_context_id on attempts/receipts.

Revision ID: 0022_frozen_gate_contexts
Revises: 0021_submit_outbox_events
Create Date: 2026-04-30
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0022_frozen_gate_contexts"
down_revision = "0021_submit_outbox_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "frozen_gate_contexts",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("bot_instance_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=256), nullable=False),
        sa.Column("context_hash", sa.String(length=64), nullable=False),
        sa.Column("context_signature", sa.String(length=128), nullable=False),
        sa.Column("canonical_context", sa.JSON(), nullable=True),
        sa.Column("runtime_version", sa.String(length=64), nullable=True),
        sa.Column("policy_version_id", sa.String(length=64), nullable=True),
        sa.Column("broker_snapshot_hash", sa.String(length=64), nullable=True),
        sa.Column("risk_context_hash", sa.String(length=64), nullable=True),
        sa.Column("approved_volume", sa.Float(), nullable=True),
        sa.Column("approved_price", sa.Float(), nullable=True),
        sa.Column("approved_sl", sa.Float(), nullable=True),
        sa.Column("approved_tp", sa.Float(), nullable=True),
        sa.Column("max_slippage_pips", sa.Float(), nullable=True),
        sa.Column("max_price_deviation_bps", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("bot_instance_id", "idempotency_key", name="uq_frozen_gate_context_bot_idem"),
    )
    op.create_index("ix_frozen_gate_contexts_bot_instance_id", "frozen_gate_contexts", ["bot_instance_id"], unique=False)

    op.add_column("broker_order_attempts", sa.Column("frozen_context_id", sa.String(length=64), nullable=True))
    op.create_index("ix_broker_order_attempts_frozen_context_id", "broker_order_attempts", ["frozen_context_id"], unique=False)

    op.add_column("broker_execution_receipts", sa.Column("frozen_context_id", sa.String(length=64), nullable=True))
    op.create_index("ix_broker_execution_receipts_frozen_context_id", "broker_execution_receipts", ["frozen_context_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_broker_execution_receipts_frozen_context_id", table_name="broker_execution_receipts")
    op.drop_column("broker_execution_receipts", "frozen_context_id")

    op.drop_index("ix_broker_order_attempts_frozen_context_id", table_name="broker_order_attempts")
    op.drop_column("broker_order_attempts", "frozen_context_id")

    op.drop_index("ix_frozen_gate_contexts_bot_instance_id", table_name="frozen_gate_contexts")
    op.drop_table("frozen_gate_contexts")
