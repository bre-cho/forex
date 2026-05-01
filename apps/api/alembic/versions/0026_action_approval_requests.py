"""Add action approval requests for P1.3 RBAC workflow.

Revision ID: 0026_action_approval_requests
Revises: 0025_provider_certification_lifecycle
Create Date: 2026-05-01
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0026_action_approval_requests"
down_revision = "0025_provider_certification_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "action_approval_requests",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("bot_instance_id", sa.String(length=64), nullable=True),
        sa.Column("action_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("requested_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("approved_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("rejected_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("reason", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("request_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("decision_note", sa.String(length=512), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_action_approval_requests_workspace_id", "action_approval_requests", ["workspace_id"], unique=False)
    op.create_index("ix_action_approval_requests_bot_instance_id", "action_approval_requests", ["bot_instance_id"], unique=False)
    op.create_index("ix_action_approval_requests_action_type", "action_approval_requests", ["action_type"], unique=False)
    op.create_index("ix_action_approval_requests_status", "action_approval_requests", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_action_approval_requests_status", table_name="action_approval_requests")
    op.drop_index("ix_action_approval_requests_action_type", table_name="action_approval_requests")
    op.drop_index("ix_action_approval_requests_bot_instance_id", table_name="action_approval_requests")
    op.drop_index("ix_action_approval_requests_workspace_id", table_name="action_approval_requests")
    op.drop_table("action_approval_requests")
