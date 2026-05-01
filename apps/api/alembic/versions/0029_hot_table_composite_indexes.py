"""Add composite indexes on hot query paths for live trading tables.

Adds composite indexes on tables that are queried at high frequency by the
reconciliation daemon, submit-outbox recovery worker, incident monitor, and
live-start preflight checks.  These indexes avoid sequential scans on large
tables when filtering by (bot_instance_id, status) or (bot_instance_id, created_at).

Revision ID: 0029_hot_table_composite_indexes
Revises: 0028_strategy_version_governance
Create Date: 2026-05-01
"""
from __future__ import annotations

from alembic import op

revision = "0029_hot_table_composite_indexes"
down_revision = "0028_strategy_version_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # reconciliation_queue_items ─────────────────────────────────────────────
    # Primary hot query: WHERE bot_instance_id = ? AND status IN (...)
    op.create_index(
        "ix_recon_queue_bot_status",
        "reconciliation_queue_items",
        ["bot_instance_id", "status"],
    )
    # Retry scheduler: WHERE status = 'retry' AND next_retry_at <= ?
    op.create_index(
        "ix_recon_queue_status_next_retry",
        "reconciliation_queue_items",
        ["status", "next_retry_at"],
    )

    # trading_incidents ──────────────────────────────────────────────────────
    # Live-start preflight: WHERE bot_instance_id = ? AND status != 'resolved'
    #   AND severity = 'critical'
    op.create_index(
        "ix_trading_incidents_bot_status_severity",
        "trading_incidents",
        ["bot_instance_id", "status", "severity"],
    )
    # Operator dashboard: WHERE status = ? ORDER BY created_at DESC
    op.create_index(
        "ix_trading_incidents_status_created",
        "trading_incidents",
        ["status", "created_at"],
    )

    # submit_outbox ──────────────────────────────────────────────────────────
    # Stale-phase scanner: WHERE phase IN ('SUBMITTING','BROKER_SEND_STARTED')
    #   AND updated_at < ?
    op.create_index(
        "ix_submit_outbox_bot_phase_updated",
        "submit_outbox",
        ["bot_instance_id", "phase", "updated_at"],
    )

    # worker_heartbeats ──────────────────────────────────────────────────────
    # Health check: WHERE worker_name = ? ORDER BY updated_at DESC LIMIT 1
    op.create_index(
        "ix_worker_heartbeats_name_updated",
        "worker_heartbeats",
        ["worker_name", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_worker_heartbeats_name_updated", table_name="worker_heartbeats")
    op.drop_index("ix_submit_outbox_bot_phase_updated", table_name="submit_outbox")
    op.drop_index("ix_trading_incidents_status_created", table_name="trading_incidents")
    op.drop_index("ix_trading_incidents_bot_status_severity", table_name="trading_incidents")
    op.drop_index("ix_recon_queue_status_next_retry", table_name="reconciliation_queue_items")
    op.drop_index("ix_recon_queue_bot_status", table_name="reconciliation_queue_items")
