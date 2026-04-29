from __future__ import annotations

import asyncio

import pytest

from execution_service.execution_engine import ExecutionEngine
from execution_service.providers.base import OrderRequest


class _SlowProvider:
    mode = "live"
    is_connected = True

    async def connect(self):
        return None

    async def disconnect(self):
        return None

    async def get_account_info(self):
        return type("Account", (), {"equity": 1000.0})()

    async def get_candles(self, symbol: str, timeframe: str, limit: int = 200):
        return None

    async def place_order(self, request: OrderRequest):
        await asyncio.sleep(0.2)

    async def close_position(self, position_id: str):
        return None

    async def get_open_positions(self):
        return []

    async def get_trade_history(self, limit: int = 100):
        return []


@pytest.mark.asyncio
async def test_execution_engine_timeout_returns_unknown_status() -> None:
    provider = _SlowProvider()
    engine = ExecutionEngine(provider=provider, runtime_mode="paper", submit_timeout_seconds=0.01)
    engine._router.register("default", provider)

    result = await engine.place_order(
        OrderRequest(symbol="EURUSD", side="buy", volume=0.01, order_type="market")
    )

    assert result.success is False
    assert result.submit_status == "UNKNOWN"
    assert result.fill_status == "UNKNOWN"
    assert "timeout" in str(result.error_message)
