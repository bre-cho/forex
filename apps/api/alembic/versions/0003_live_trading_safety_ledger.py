"""0003 — Live trading safety ledger tables.

Adds tables required for P0-P5 production live trading:
  - trading_decision_ledger
  - pre_execution_gate_events
  - daily_trading_state
  - broker_order_events
  - broker_reconciliation_runs
  - trading_incidents
  - policy_versions
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_live_trading_safety_ledger"
down_revision = "0002_trade_lifecycle_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── trading_decision_ledger ─────────────────────────────────────────
    op.create_table(
        "trading_decision_ledger",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("bot_instance_id", sa.String(64), nullable=False, index=True),
        sa.Column("signal_id", sa.String(128), nullable=False),
        sa.Column("cycle_id", sa.String(128), nullable=True),
        sa.Column("brain_action", sa.String(16), nullable=False),
        sa.Column("brain_reason", sa.Text, nullable=True),
        sa.Column("brain_score", sa.Float, nullable=True),
        sa.Column("stage_decisions", sa.JSON, nullable=True),
        sa.Column("policy_snapshot", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint(
        "uq_decision_ledger_bot_signal",
        "trading_decision_ledger",
        ["bot_instance_id", "signal_id"],
    )

    # ── pre_execution_gate_events ───────────────────────────────────────
    op.create_table(
        "pre_execution_gate_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("bot_instance_id", sa.String(64), nullable=False, index=True),
        sa.Column("signal_id", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("gate_action", sa.String(16), nullable=False),
        sa.Column("gate_reason", sa.String(128), nullable=False),
        sa.Column("gate_details", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint(
        "uq_gate_event_idempotency",
        "pre_execution_gate_events",
        ["bot_instance_id", "idempotency_key"],
    )

    # ── daily_trading_state ─────────────────────────────────────────────
    op.create_table(
        "daily_trading_state",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("bot_instance_id", sa.String(64), nullable=False),
        sa.Column("trading_day", sa.Date, nullable=False),
        sa.Column("starting_equity", sa.Float, nullable=True),
        sa.Column("current_equity", sa.Float, nullable=True),
        sa.Column("daily_profit_amount", sa.Float, default=0.0, nullable=False),
        sa.Column("daily_loss_pct", sa.Float, default=0.0, nullable=False),
        sa.Column("trades_count", sa.Integer, default=0, nullable=False),
        sa.Column("consecutive_losses", sa.Integer, default=0, nullable=False),
        sa.Column("locked", sa.Boolean, default=False, nullable=False),
        sa.Column("lock_reason", sa.String(128), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint(
        "uq_daily_state_bot_day",
        "daily_trading_state",
        ["bot_instance_id", "trading_day"],
    )

    # ── broker_order_events ─────────────────────────────────────────────
    op.create_table(
        "broker_order_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("bot_instance_id", sa.String(64), nullable=False, index=True),
        sa.Column("broker_order_id", sa.String(128), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=True),
        sa.Column("side", sa.String(8), nullable=True),
        sa.Column("volume", sa.Float, nullable=True),
        sa.Column("price", sa.Float, nullable=True),
        sa.Column("payload", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # ── broker_reconciliation_runs ──────────────────────────────────────
    op.create_table(
        "broker_reconciliation_runs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("bot_instance_id", sa.String(64), nullable=False, index=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("open_positions_broker", sa.Integer, nullable=True),
        sa.Column("open_positions_db", sa.Integer, nullable=True),
        sa.Column("mismatches", sa.JSON, nullable=True),
        sa.Column("repaired", sa.Integer, default=0, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── trading_incidents ───────────────────────────────────────────────
    op.create_table(
        "trading_incidents",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("bot_instance_id", sa.String(64), nullable=False, index=True),
        sa.Column("incident_type", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False, default="warning"),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("detail", sa.Text, nullable=True),
        sa.Column("status", sa.String(32), nullable=False, default="open"),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # ── policy_versions ─────────────────────────────────────────────────
    op.create_table(
        "policy_versions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("bot_instance_id", sa.String(64), nullable=False, index=True),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("policy_snapshot", sa.JSON, nullable=False),
        sa.Column("change_reason", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint(
        "uq_policy_version_bot",
        "policy_versions",
        ["bot_instance_id", "version"],
    )


def downgrade() -> None:
    op.drop_table("policy_versions")
    op.drop_table("trading_incidents")
    op.drop_table("broker_reconciliation_runs")
    op.drop_table("broker_order_events")
    op.drop_table("daily_trading_state")
    op.drop_table("pre_execution_gate_events")
    op.drop_table("trading_decision_ledger")
