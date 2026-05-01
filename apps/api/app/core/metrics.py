"""Prometheus metrics definitions — P1.1 Observability Pack.

All metrics are defined here to avoid duplicate registration issues.

Usage:
    from app.core.metrics import (
        BOT_RUNTIME_HEARTBEAT_AGE,
        ORDER_UNKNOWN_TOTAL,
        ...
    )
    ORDER_UNKNOWN_TOTAL.labels(bot_id=bot_id, provider="ctrader").inc()
"""
from __future__ import annotations

try:
    from prometheus_client import Counter, Gauge, Histogram, CollectorRegistry, REGISTRY

    _registry = REGISTRY

    # ─── Worker Health ─────────────────────────────────────────────────────────
    BOT_RUNTIME_HEARTBEAT_AGE = Gauge(
        "bot_runtime_heartbeat_age_seconds",
        "Seconds since last bot runtime heartbeat",
        labelnames=["bot_id"],
        registry=_registry,
    )

    WORKER_HEARTBEAT_AGE = Gauge(
        "worker_heartbeat_age_seconds",
        "Seconds since last worker heartbeat",
        labelnames=["worker_name"],
        registry=_registry,
    )

    # ─── Broker / Market Data ──────────────────────────────────────────────────
    BROKER_QUOTE_AGE = Gauge(
        "broker_quote_age_seconds",
        "Age of the last broker quote received",
        labelnames=["bot_id", "provider", "symbol"],
        registry=_registry,
    )

    PROVIDER_CERTIFICATION_STATUS = Gauge(
        "provider_certification_status",
        "1 if provider certification is valid, 0 otherwise",
        labelnames=["bot_id", "provider"],
        registry=_registry,
    )

    # ─── Order Execution ──────────────────────────────────────────────────────
    ORDER_SUBMIT_LATENCY = Histogram(
        "order_submit_latency_ms",
        "Latency from gate approval to broker acknowledgement in milliseconds",
        labelnames=["bot_id", "provider", "symbol"],
        buckets=[10, 50, 100, 250, 500, 1000, 2500, 5000, 10000],
        registry=_registry,
    )

    ORDER_PLACED_TOTAL = Counter(
        "order_placed_total",
        "Total orders placed (gate approved + submitted)",
        labelnames=["bot_id", "provider", "symbol", "side"],
        registry=_registry,
    )

    ORDER_REJECTED_TOTAL = Counter(
        "order_rejected_total",
        "Total orders rejected by broker",
        labelnames=["bot_id", "provider", "symbol"],
        registry=_registry,
    )

    ORDER_SLIPPAGE_PIPS = Histogram(
        "order_slippage_pips",
        "Slippage in pips between requested and filled price",
        labelnames=["bot_id", "provider", "symbol"],
        buckets=[0, 0.1, 0.5, 1, 2, 5, 10, 20],
        registry=_registry,
    )

    # ─── UNKNOWN / Reconciliation ─────────────────────────────────────────────
    ORDER_UNKNOWN_TOTAL = Counter(
        "order_unknown_total",
        "Total orders that entered UNKNOWN state",
        labelnames=["bot_id", "provider"],
        registry=_registry,
    )

    RECONCILIATION_OVERDUE_TOTAL = Counter(
        "reconciliation_overdue_total",
        "Total reconciliation queue items that reached deadline/max-retries",
        labelnames=["bot_id"],
        registry=_registry,
    )

    RECONCILIATION_RESOLVED_TOTAL = Counter(
        "reconciliation_resolved_total",
        "Total UNKNOWN orders resolved via reconciliation",
        labelnames=["bot_id", "provider", "resolution_code"],
        registry=_registry,
    )

    RECONCILIATION_QUEUE_DEPTH = Gauge(
        "reconciliation_queue_depth",
        "Current number of unresolved items in the reconciliation queue",
        registry=_registry,
    )

    # ─── Daily Lock ───────────────────────────────────────────────────────────
    DAILY_LOCK_ACTIONS_TOTAL = Counter(
        "daily_lock_actions_total",
        "Total daily lock actions triggered",
        labelnames=["bot_id", "action", "reason"],
        registry=_registry,
    )

    # ─── Gate ─────────────────────────────────────────────────────────────────
    GATE_BLOCK_TOTAL = Counter(
        "pre_execution_gate_block_total",
        "Total times PreExecutionGate returned BLOCK",
        labelnames=["bot_id", "reason"],
        registry=_registry,
    )

    GATE_ALLOW_TOTAL = Counter(
        "pre_execution_gate_allow_total",
        "Total times PreExecutionGate returned ALLOW",
        labelnames=["bot_id"],
        registry=_registry,
    )

    # ─── Incident ─────────────────────────────────────────────────────────────
    INCIDENT_CREATED_TOTAL = Counter(
        "trading_incident_created_total",
        "Total trading incidents created",
        labelnames=["incident_type", "severity"],
        registry=_registry,
    )

    # ─── Account / Equity ─────────────────────────────────────────────────────
    ACCOUNT_EQUITY_GAUGE = Gauge(
        "account_equity",
        "Live account equity synced from broker",
        labelnames=["bot_id", "provider"],
        registry=_registry,
    )

    ACCOUNT_BALANCE_GAUGE = Gauge(
        "account_balance",
        "Live account balance synced from broker",
        labelnames=["bot_id", "provider"],
        registry=_registry,
    )

    EQUITY_DRIFT_GAUGE = Gauge(
        "account_equity_drift_pct",
        "Absolute equity drift percentage between broker and internal tracking",
        labelnames=["bot_id", "provider"],
        registry=_registry,
    )

    OPEN_POSITIONS_GAUGE = Gauge(
        "open_positions_total",
        "Number of currently open positions per bot",
        labelnames=["bot_id", "provider", "symbol"],
        registry=_registry,
    )

    DAILY_PNL_GAUGE = Gauge(
        "daily_pnl",
        "Daily profit/loss amount (positive = profit, negative = loss)",
        labelnames=["bot_id", "provider"],
        registry=_registry,
    )

    _PROMETHEUS_AVAILABLE = True

except ImportError:
    _PROMETHEUS_AVAILABLE = False

    # Provide no-op stubs so the rest of the app runs without prometheus_client.
    class _Noop:
        def labels(self, **kwargs) -> "_Noop":
            return self

        def inc(self, amount: float = 1) -> None:
            pass

        def set(self, value: float) -> None:
            pass

        def observe(self, value: float) -> None:
            pass

        def time(self):
            import contextlib
            return contextlib.nullcontext()

    _noop = _Noop()

    BOT_RUNTIME_HEARTBEAT_AGE = _noop
    WORKER_HEARTBEAT_AGE = _noop
    BROKER_QUOTE_AGE = _noop
    PROVIDER_CERTIFICATION_STATUS = _noop
    ORDER_SUBMIT_LATENCY = _noop
    ORDER_PLACED_TOTAL = _noop
    ORDER_REJECTED_TOTAL = _noop
    ORDER_SLIPPAGE_PIPS = _noop
    ORDER_UNKNOWN_TOTAL = _noop
    RECONCILIATION_OVERDUE_TOTAL = _noop
    RECONCILIATION_RESOLVED_TOTAL = _noop
    RECONCILIATION_QUEUE_DEPTH = _noop
    DAILY_LOCK_ACTIONS_TOTAL = _noop
    GATE_BLOCK_TOTAL = _noop
    GATE_ALLOW_TOTAL = _noop
    INCIDENT_CREATED_TOTAL = _noop
    ACCOUNT_EQUITY_GAUGE = _noop
    ACCOUNT_BALANCE_GAUGE = _noop
    EQUITY_DRIFT_GAUGE = _noop
    OPEN_POSITIONS_GAUGE = _noop
    DAILY_PNL_GAUGE = _noop


def prometheus_available() -> bool:
    return _PROMETHEUS_AVAILABLE
