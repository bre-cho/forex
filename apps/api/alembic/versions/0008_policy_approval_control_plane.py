"""0008 - policy approval control plane tables/columns."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_policy_approval_control_plane"
down_revision = "0007_broker_execution_receipts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("policy_versions", sa.Column("status", sa.String(32), nullable=False, server_default="draft"))
    op.add_column("policy_versions", sa.Column("approved_by", sa.String(36), nullable=True))
    op.add_column("policy_versions", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("policy_versions", sa.Column("activated_by", sa.String(36), nullable=True))
    op.add_column("policy_versions", sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "policy_approvals",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("bot_instance_id", sa.String(64), nullable=False, index=True),
        sa.Column("policy_version", sa.Integer, nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("actor_user_id", sa.String(36), nullable=True),
        sa.Column("note", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("policy_approvals")
    op.drop_column("policy_versions", "activated_at")
    op.drop_column("policy_versions", "activated_by")
    op.drop_column("policy_versions", "approved_at")
    op.drop_column("policy_versions", "approved_by")
    op.drop_column("policy_versions", "status")
