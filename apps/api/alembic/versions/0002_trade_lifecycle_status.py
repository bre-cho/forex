"""add trade lifecycle status fields

Revision ID: 0002_trade_lifecycle_status
Revises: 0001_initial_schema
Create Date: 2026-04-26 00:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0002_trade_lifecycle_status"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "trades",
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
    )
    op.add_column(
        "trades",
        sa.Column("closed_volume", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "trades",
        sa.Column("remaining_volume", sa.Float(), nullable=False, server_default="0"),
    )



def downgrade() -> None:
    op.drop_column("trades", "remaining_volume")
    op.drop_column("trades", "closed_volume")
    op.drop_column("trades", "status")
