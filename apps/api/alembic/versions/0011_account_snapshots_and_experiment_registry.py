"""0011 - broker account snapshots and experiment registry."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_account_snapshots_and_experiment_registry"
down_revision = "0010_order_state_machine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "broker_account_snapshots",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("bot_instance_id", sa.String(64), nullable=False),
        sa.Column("broker", sa.String(32), nullable=False),
        sa.Column("account_id", sa.String(128), nullable=True),
        sa.Column("balance", sa.Float, nullable=True),
        sa.Column("equity", sa.Float, nullable=True),
        sa.Column("margin", sa.Float, nullable=True),
        sa.Column("free_margin", sa.Float, nullable=True),
        sa.Column("margin_level", sa.Float, nullable=True),
        sa.Column("currency", sa.String(16), nullable=True),
        sa.Column("raw_response", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_broker_account_snapshots_bot_created",
        "broker_account_snapshots",
        ["bot_instance_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "strategy_experiments",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("bot_instance_id", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("stage", sa.String(32), nullable=False, server_default="DRAFT"),
        sa.Column("strategy_snapshot", sa.JSON, nullable=True),
        sa.Column("policy_snapshot", sa.JSON, nullable=True),
        sa.Column("metrics_snapshot", sa.JSON, nullable=True),
        sa.Column("note", sa.String(512), nullable=True),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("updated_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("bot_instance_id", "version", name="uq_strategy_experiments_bot_version"),
    )
    op.create_index(
        "ix_strategy_experiments_bot_stage",
        "strategy_experiments",
        ["bot_instance_id", "stage"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_strategy_experiments_bot_stage", table_name="strategy_experiments")
    op.drop_table("strategy_experiments")
    op.drop_index("ix_broker_account_snapshots_bot_created", table_name="broker_account_snapshots")
    op.drop_table("broker_account_snapshots")
