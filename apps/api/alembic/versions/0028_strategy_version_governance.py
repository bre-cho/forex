"""Add strategy version governance lifecycle fields.

Revision ID: 0028_strategy_version_governance
Revises: 0027_broker_connection_credential_scope
Create Date: 2026-05-01
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0028_strategy_version_governance"
down_revision = "0027_broker_connection_credential_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("strategy_versions", sa.Column("stage", sa.String(length=32), nullable=False, server_default="DRAFT"))
    op.add_column("strategy_versions", sa.Column("approved_by", sa.String(length=36), nullable=True))
    op.add_column("strategy_versions", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("strategy_versions", "approved_at")
    op.drop_column("strategy_versions", "approved_by")
    op.drop_column("strategy_versions", "stage")
