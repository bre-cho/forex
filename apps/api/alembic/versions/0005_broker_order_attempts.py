"""0005 - broker_order_attempts for atomic live order lifecycle."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_broker_order_attempts"
down_revision = "0004_order_idempotency_reservations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "broker_order_attempts",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("bot_instance_id", sa.String(64), nullable=False, index=True),
        sa.Column("signal_id", sa.String(128), nullable=False),
        sa.Column("brain_cycle_id", sa.String(128), nullable=True),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("broker", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("volume", sa.Float, nullable=False),
        sa.Column("request_payload", sa.JSON, nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING_SUBMIT"),
        sa.Column("broker_order_id", sa.String(128), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint(
        "uq_broker_attempt_bot_key",
        "broker_order_attempts",
        ["bot_instance_id", "idempotency_key"],
    )


def downgrade() -> None:
    op.drop_table("broker_order_attempts")
