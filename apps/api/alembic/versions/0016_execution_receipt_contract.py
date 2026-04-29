"""Expand broker execution receipt contract fields.

Revision ID: 0016_execution_receipt_contract
Revises: 0015_broker_attempt_gate_context_hash
Create Date: 2026-04-29
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0016_execution_receipt_contract"
down_revision = "0015_broker_attempt_gate_context_hash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("broker_execution_receipts", sa.Column("client_order_id", sa.String(length=256), nullable=True))
    op.add_column("broker_execution_receipts", sa.Column("account_id", sa.String(length=128), nullable=True))
    op.add_column("broker_execution_receipts", sa.Column("server_time", sa.Float(), nullable=True))
    op.add_column("broker_execution_receipts", sa.Column("latency_ms", sa.Float(), nullable=False, server_default="0"))
    op.add_column("broker_execution_receipts", sa.Column("raw_response_hash", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("broker_execution_receipts", "raw_response_hash")
    op.drop_column("broker_execution_receipts", "latency_ms")
    op.drop_column("broker_execution_receipts", "server_time")
    op.drop_column("broker_execution_receipts", "account_id")
    op.drop_column("broker_execution_receipts", "client_order_id")
