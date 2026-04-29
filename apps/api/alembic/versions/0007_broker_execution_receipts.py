"""0007 - broker_execution_receipts table."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_broker_execution_receipts"
down_revision = "0006_order_state_transitions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "broker_execution_receipts",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("bot_instance_id", sa.String(64), nullable=False, index=True),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("broker", sa.String(32), nullable=False),
        sa.Column("broker_order_id", sa.String(128), nullable=True),
        sa.Column("broker_position_id", sa.String(128), nullable=True),
        sa.Column("broker_deal_id", sa.String(128), nullable=True),
        sa.Column("submit_status", sa.String(32), nullable=False),
        sa.Column("fill_status", sa.String(32), nullable=False),
        sa.Column("requested_volume", sa.Float, nullable=False),
        sa.Column("filled_volume", sa.Float, nullable=False),
        sa.Column("avg_fill_price", sa.Float, nullable=True),
        sa.Column("commission", sa.Float, nullable=False, server_default="0"),
        sa.Column("raw_response", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("broker_execution_receipts")
