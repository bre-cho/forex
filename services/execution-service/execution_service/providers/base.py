"""Abstract base class for broker providers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd


@dataclass
class OrderRequest:
    symbol: str
    side: str          # 'buy' | 'sell'
    volume: float
    order_type: str    # 'market' | 'limit' | 'stop'
    price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    comment: str = ""
    client_order_id: str = ""
    idempotency_key: str = ""


@dataclass
class OrderResult:
    order_id: str
    symbol: str
    side: str
    volume: float
    fill_price: float
    commission: float
    success: bool
    client_order_id: Optional[str] = None
    broker_order_id: Optional[str] = None
    error_message: Optional[str] = None
    submit_status: str = "UNKNOWN"   # ACKED | REJECTED | UNKNOWN
    fill_status: str = "UNKNOWN"     # FILLED | PARTIAL | PENDING | UNKNOWN
    broker_position_id: Optional[str] = None
    broker_deal_id: Optional[str] = None
    account_id: Optional[str] = None
    server_time: Optional[float] = None
    latency_ms: float = 0.0
    raw_response_hash: Optional[str] = None
    raw_response: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionReceipt:
    idempotency_key: str
    broker_order_id: Optional[str]
    broker_position_id: Optional[str]
    broker_deal_id: Optional[str]
    submit_status: str
    fill_status: str
    requested_volume: float
    filled_volume: float
    avg_fill_price: Optional[float]
    commission: float
    raw_response: Dict[str, Any]
    latency_ms: float


@dataclass
class AccountInfo:
    balance: float
    equity: float
    margin: float
    free_margin: float
    margin_level: float
    currency: str




@dataclass
class PreExecutionContext:
    bot_instance_id: str
    runtime_mode: str
    provider_mode: str
    broker_connected: bool
    market_data_ok: bool
    data_age_seconds: float
    spread_pips: float
    confidence: float
    rr: float
    open_positions: int
    daily_profit_amount: float
    daily_loss_pct: float
    consecutive_losses: int
    daily_locked: bool
    kill_switch: bool
    idempotency_key: str
    brain_cycle_id: str
    account_id: Optional[str] = None
    broker_name: str = ""
    order_type: str = "market"
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    policy_version: str = ""
    margin_usage_pct: float = 0.0
    free_margin_after_order: float = 0.0
    account_exposure_pct: float = 0.0
    symbol_exposure_pct: float = 0.0
    correlated_usd_exposure_pct: float = 0.0
    portfolio_daily_loss_pct: float = 0.0
    portfolio_open_positions: int = 0
    portfolio_kill_switch: bool = False
    policy_snapshot: Dict[str, Any] = field(default_factory=dict)
    gate_context: Dict[str, Any] = field(default_factory=dict)
    context_hash: str = ""


@dataclass
class ExecutionCommand:
    request: OrderRequest
    intent: Dict[str, Any]
    pre_execution_context: PreExecutionContext
    idempotency_key: str
    brain_cycle_id: str

class BrokerProvider(ABC):
    """Abstract broker provider — all concrete providers must implement this."""

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the broker."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection to the broker."""

    @abstractmethod
    async def get_account_info(self) -> AccountInfo:
        """Return current account information."""

    @abstractmethod
    async def get_candles(self, symbol: str, timeframe: str, limit: int = 200) -> pd.DataFrame:
        """Return OHLCV candle data."""

    @abstractmethod
    async def place_order(self, request: OrderRequest) -> OrderResult:
        """Place a market or pending order."""

    @abstractmethod
    async def close_position(self, position_id: str) -> OrderResult:
        """Close an open position by ID."""

    @abstractmethod
    async def get_open_positions(self) -> List[Dict[str, Any]]:
        """Return list of currently open positions."""

    @abstractmethod
    async def get_trade_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Return closed trade history."""

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Return True if the provider is currently connected."""

    # ------------------------------------------------------------------
    # Optional methods required for live mode (raise NotImplementedError
    # by default — live readiness guard checks these).
    # ------------------------------------------------------------------

    async def get_instrument_spec(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Return instrument/symbol specification dict.

        Live mode requires a real implementation from the provider.
        """
        raise NotImplementedError(f"get_instrument_spec not implemented for {type(self).__name__}")

    async def estimate_margin(self, symbol: str, side: str, volume: float, price: float) -> float:
        """Estimate required margin for an order in account currency.

        Live mode should use broker's own margin calculator if available.
        """
        raise NotImplementedError(f"estimate_margin not implemented for {type(self).__name__}")

    async def get_order_by_client_id(self, client_order_id: str) -> Optional[Dict[str, Any]]:
        """Look up an order by client/idempotency/comment id.

        Required for UnknownOrderReconciler in live mode.
        Returns None if not found.
        """
        raise NotImplementedError(f"get_order_by_client_id not implemented for {type(self).__name__}")

    async def get_executions_by_client_id(self, client_order_id: str) -> List[Dict[str, Any]]:
        """Return list of executions/deals for a given client order id.

        Required for UnknownOrderReconciler in live mode.
        Returns [] if not found.
        """
        raise NotImplementedError(f"get_executions_by_client_id not implemented for {type(self).__name__}")

    async def close_all_positions(self, symbol: Optional[str] = None) -> List[OrderResult]:
        """Close all open positions, optionally filtered by symbol."""
        raise NotImplementedError(f"close_all_positions not implemented for {type(self).__name__}")

    async def get_server_time(self) -> Optional[float]:
        """Return broker server UTC timestamp (epoch seconds). None if unsupported."""
        return None

    async def get_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Return current bid/ask quote for symbol. None if unsupported."""
        return None

    @property
    def supports_client_order_id(self) -> bool:
        """Return True if the provider can accept and look up orders by client order id.

        Execution service treats this as False-by-default; providers that implement
        get_order_by_client_id must override this property to return True.
        Live mode must fail if this returns False (no idempotency audit trail).
        """
        return False

