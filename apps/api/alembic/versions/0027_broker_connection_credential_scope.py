"""Add broker connection credential scope.

Revision ID: 0027_broker_connection_credential_scope
Revises: 0026_action_approval_requests
Create Date: 2026-05-01
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0027_broker_connection_credential_scope"
down_revision = "0026_action_approval_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "broker_connections",
        sa.Column("credential_scope", sa.String(length=16), nullable=False, server_default="demo"),
    )
    op.create_index(
        "ix_broker_connections_credential_scope",
        "broker_connections",
        ["credential_scope"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_broker_connections_credential_scope", table_name="broker_connections")
    op.drop_column("broker_connections", "credential_scope")
