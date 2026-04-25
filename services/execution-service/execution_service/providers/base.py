"""Abstract base class for broker providers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
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


@dataclass
class AccountInfo:
    balance: float
    equity: float
    margin: float
    free_margin: float
    margin_level: float
    currency: str


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
