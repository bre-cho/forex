from __future__ import annotations

import sys
import types

import pandas as pd
import pytest

from execution_service.providers.base import OrderRequest
from execution_service.providers.ctrader import CTraderProvider
from execution_service.providers.ctrader_live import CTraderLiveProvider


@pytest.mark.asyncio
async def test_live_connect_fails_when_execution_adapter_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("trading_core.engines.ctrader_provider")

    class FakeDataProvider:
        def __init__(self, symbol: str, timeframe: str):
            self.symbol = symbol
            self.timeframe = timeframe

        def get_candles(self, limit: int = 1) -> pd.DataFrame:
            return pd.DataFrame([{"open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}])

    module.CTraderDataProvider = FakeDataProvider
    monkeypatch.setitem(sys.modules, "trading_core", types.ModuleType("trading_core"))
    monkeypatch.setitem(sys.modules, "trading_core.engines", types.ModuleType("trading_core.engines"))
    monkeypatch.setitem(sys.modules, "trading_core.engines.ctrader_provider", module)

    provider = CTraderLiveProvider(
        client_id="id",
        client_secret="secret",
        access_token="token",
        refresh_token="refresh",
        account_id=123,
    )

    with pytest.raises(RuntimeError, match="execution adapter unavailable"):
        await provider.connect()


@pytest.mark.asyncio
async def test_place_order_delegates_to_execution_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("trading_core.engines.ctrader_provider")

    class FakeDataProvider:
        def __init__(self, symbol: str, timeframe: str):
            self.symbol = symbol
            self.timeframe = timeframe

        async def get_account_info(self) -> dict:
            return {
                "balance": 1000,
                "equity": 1000,
                "margin": 0,
                "free_margin": 1000,
                "margin_level": 0,
                "currency": "USD",
            }

        def get_candles(self, limit: int = 1) -> pd.DataFrame:
            return pd.DataFrame([{"open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}])

        async def place_market_order(self, **kwargs) -> dict:
            return {
                "orderId": "ORD-1",
                "executionPrice": 1.2345,
                "commission": 0.2,
                "volume": kwargs.get("volume", 0),
            }

        async def close_position(self, position_id: int) -> dict:
            return {"executionPrice": 1.2340, "volume": 0.01}

        async def get_positions(self) -> list[dict]:
            return []

        async def get_history(self, limit: int = 100) -> list[dict]:
            return []

    module.CTraderDataProvider = FakeDataProvider
    monkeypatch.setitem(sys.modules, "trading_core", types.ModuleType("trading_core"))
    monkeypatch.setitem(sys.modules, "trading_core.engines", types.ModuleType("trading_core.engines"))
    monkeypatch.setitem(sys.modules, "trading_core.engines.ctrader_provider", module)

    provider = CTraderProvider(
        client_id="id",
        client_secret="secret",
        access_token="token",
        refresh_token="refresh",
        account_id=0,
        live=False,
    )

    await provider.connect()
    result = await provider.place_order(
        OrderRequest(
            symbol="EURUSD",
            side="buy",
            volume=0.01,
            order_type="market",
            stop_loss=None,
            take_profit=None,
        )
    )

    assert result.success is True
    assert result.order_id == "ORD-1"
    assert result.fill_price == 1.2345
    assert provider.mode == "demo"
