"""Paper trading provider — simulates orders without real broker connection."""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, List, Optional

import pandas as pd

from .base import AccountInfo, BrokerProvider, OrderRequest, OrderResult

logger = logging.getLogger(__name__)


class PaperProvider(BrokerProvider):
    """
    In-memory paper trading provider.
    Adapted from backend/engine/data_provider.py MockDataProvider.
    """

    def __init__(
        self,
        symbol: str = "EURUSD",
        initial_balance: float = 10_000.0,
    ) -> None:
        self.symbol = symbol
        self.provider_name = "paper"
        self.mode = "paper"
        self._balance = initial_balance
        self._equity = initial_balance
        self._positions: List[Dict[str, Any]] = []
        self._history: List[Dict[str, Any]] = []
        self._connected = False
        self._last_candles: Optional[pd.DataFrame] = None

    async def connect(self) -> None:
        self._connected = True
        logger.info("PaperProvider connected (balance=%.2f)", self._balance)

    async def disconnect(self) -> None:
        self._connected = False

    async def get_account_info(self) -> AccountInfo:
        used_margin = sum(p.get("margin", 0) for p in self._positions)
        return AccountInfo(
            balance=self._balance,
            equity=self._equity,
            margin=used_margin,
            free_margin=self._equity - used_margin,
            margin_level=(self._equity / used_margin * 100) if used_margin else 0.0,
            currency="USD",
        )

    async def get_candles(self, symbol: str, timeframe: str, limit: int = 200) -> pd.DataFrame:
        import numpy as np

        # Use a time-based seed so each call produces different (but plausible) data.
        seed = int(time.time() * 1000) % (2 ** 31)
        rng = np.random.default_rng(seed=seed)
        closes = 1.10000 + rng.normal(0, 0.0005, limit).cumsum()
        opens = closes - rng.normal(0, 0.0002, limit)
        highs = np.maximum(closes, opens) + rng.uniform(0, 0.0003, limit)
        lows = np.minimum(closes, opens) - rng.uniform(0, 0.0003, limit)
        volumes = rng.integers(100, 1000, limit).astype(float)

        df = pd.DataFrame(
            {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes}
        )
        self._last_candles = df
        return df

    def _current_mid_price(self) -> float:
        """Return the latest close price from cached candles, or a neutral default."""
        if self._last_candles is not None and not self._last_candles.empty:
            return float(self._last_candles["close"].iloc[-1])
        return 1.10000

    async def place_order(self, request: OrderRequest) -> OrderResult:
        order_id = str(uuid.uuid4())
        # Use the explicitly supplied price when given; otherwise derive the
        # mid-price from the most recently fetched candle series so that paper
        # fill prices are symbol-aware and vary over time.
        fill_price = float(request.price) if request.price is not None and float(request.price) > 0 else self._current_mid_price()
        position = {
            "position_id": order_id,
            "symbol": request.symbol,
            "side": request.side,
            "volume": request.volume,
            "open_price": fill_price,
            "stop_loss": request.stop_loss,
            "take_profit": request.take_profit,
            "margin": request.volume * 1000 * fill_price / 100,
        }
        self._positions.append(position)
        logger.info(
            "Paper order: %s %s %.2f @ %.5f",
            request.side, request.symbol, request.volume, fill_price,
        )
        return OrderResult(
            order_id=order_id,
            symbol=request.symbol,
            side=request.side,
            volume=request.volume,
            fill_price=fill_price,
            commission=0.0,
            success=True,
        )

    async def close_position(self, position_id: str) -> OrderResult:
        position = next((p for p in self._positions if p["position_id"] == position_id), None)
        if position is None:
            return OrderResult(
                order_id=position_id, symbol="", side="", volume=0,
                fill_price=0, commission=0, success=False,
                error_message="Position not found",
            )
        self._positions = [p for p in self._positions if p["position_id"] != position_id]
        close_price = self._current_mid_price()
        self._history.append({**position, "close_price": close_price})
        return OrderResult(
            order_id=position_id, symbol=position["symbol"], side="close",
            volume=position["volume"], fill_price=close_price,
            commission=0.0, success=True,
        )

    async def get_open_positions(self) -> List[Dict[str, Any]]:
        return list(self._positions)

    async def get_trade_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self._history[-limit:]

    async def health_check(self) -> Dict[str, Any]:
        return {
            "status": "healthy" if self._connected else "disconnected",
            "reason": "" if self._connected else "provider_not_connected",
        }

    @property
    def is_connected(self) -> bool:
        return self._connected
