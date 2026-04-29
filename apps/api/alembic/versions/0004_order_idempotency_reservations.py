"""0004 - order idempotency reservations and gate event uniqueness relaxation."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_order_idempotency_reservations"
down_revision = "0003_live_trading_safety_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "order_idempotency_reservations",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("bot_instance_id", sa.String(64), nullable=False, index=True),
        sa.Column("signal_id", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("brain_cycle_id", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="reserved"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint(
        "uq_order_idempotency_bot_key",
        "order_idempotency_reservations",
        ["bot_instance_id", "idempotency_key"],
    )

    op.drop_constraint("uq_gate_event_idempotency", "pre_execution_gate_events", type_="unique")
    op.create_index(
        "ix_gate_event_bot_key",
        "pre_execution_gate_events",
        ["bot_instance_id", "idempotency_key"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_gate_event_bot_key", table_name="pre_execution_gate_events")
    op.create_unique_constraint(
        "uq_gate_event_idempotency",
        "pre_execution_gate_events",
        ["bot_instance_id", "idempotency_key"],
    )
    op.drop_table("order_idempotency_reservations")
