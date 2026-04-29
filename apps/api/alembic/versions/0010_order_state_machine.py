"""0010 - order state machine indexes and constraints helper migration."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_order_state_machine"
down_revision = "0009_daily_profit_lock_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_broker_order_attempts_bot_state",
        "broker_order_attempts",
        ["bot_instance_id", "current_state"],
        unique=False,
    )
    op.create_index(
        "ix_order_state_transitions_bot_idem",
        "order_state_transitions",
        ["bot_instance_id", "idempotency_key"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_order_state_transitions_bot_idem", table_name="order_state_transitions")
    op.drop_index("ix_broker_order_attempts_bot_state", table_name="broker_order_attempts")
