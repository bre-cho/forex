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


@dataclass
class OrderResult:
    order_id: str
    symbol: str
    side: str
    volume: float
    fill_price: float
    commission: float
    success: bool
    error_message: Optional[str] = None
    submit_status: str = "UNKNOWN"   # ACKED | REJECTED | UNKNOWN
    fill_status: str = "UNKNOWN"     # FILLED | PARTIAL | PENDING | UNKNOWN
    broker_position_id: Optional[str] = None
    broker_deal_id: Optional[str] = None
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
    margin_usage_pct: float = 0.0
    free_margin_after_order: float = 0.0
    account_exposure_pct: float = 0.0
    symbol_exposure_pct: float = 0.0
    correlated_usd_exposure_pct: float = 0.0
    portfolio_daily_loss_pct: float = 0.0
    portfolio_open_positions: int = 0
    portfolio_kill_switch: bool = False
    policy_snapshot: Dict[str, Any] = field(default_factory=dict)


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
