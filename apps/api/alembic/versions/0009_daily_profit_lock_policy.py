"""0009 - daily profit lock events and order attempt current_state."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_daily_profit_lock_policy"
down_revision = "0008_policy_approval_control_plane"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "broker_order_attempts",
        sa.Column("current_state", sa.String(64), nullable=False, server_default="INTENT_CREATED"),
    )

    op.create_table(
        "daily_lock_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("bot_instance_id", sa.String(64), nullable=False, index=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("lock_action", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(256), nullable=True),
        sa.Column("payload", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("daily_lock_events")
    op.drop_column("broker_order_attempts", "current_state")
