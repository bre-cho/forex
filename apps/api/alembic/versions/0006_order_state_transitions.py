"""0006 - add order_state_transitions lifecycle table."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_order_state_transitions"
down_revision = "0005_broker_order_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "order_state_transitions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("bot_instance_id", sa.String(64), nullable=False, index=True),
        sa.Column("signal_id", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("from_state", sa.String(64), nullable=True),
        sa.Column("to_state", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("detail", sa.Text, nullable=True),
        sa.Column("payload", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("order_state_transitions")
