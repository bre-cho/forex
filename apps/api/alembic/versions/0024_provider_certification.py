"""Add provider_certifications table.

Revision ID: 0024_provider_certification
Revises: 0023_reconciliation_attempt_events
Create Date: 2026-04-30
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0024_provider_certification"
down_revision = "0023_reconciliation_attempt_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_certifications",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("bot_instance_id", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False, server_default="live"),
        sa.Column("account_id", sa.String(length=128), nullable=True),
        sa.Column("symbol", sa.String(length=32), nullable=True),
        sa.Column("live_certified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("certification_hash", sa.String(length=128), nullable=True),
        sa.Column("required_checks", sa.JSON(), nullable=True),
        sa.Column("checks_passed", sa.JSON(), nullable=True),
        sa.Column("checks", sa.JSON(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("actor_user_id", sa.String(length=36), nullable=True),
        sa.Column("certified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_provider_certifications_bot_instance_id", "provider_certifications", ["bot_instance_id"], unique=False)
    op.create_index("ix_provider_certifications_provider", "provider_certifications", ["provider"], unique=False)
    op.create_index("ix_provider_certifications_live_certified", "provider_certifications", ["live_certified"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_provider_certifications_live_certified", table_name="provider_certifications")
    op.drop_index("ix_provider_certifications_provider", table_name="provider_certifications")
    op.drop_index("ix_provider_certifications_bot_instance_id", table_name="provider_certifications")
    op.drop_table("provider_certifications")
