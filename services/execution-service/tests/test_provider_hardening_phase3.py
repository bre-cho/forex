from __future__ import annotations

import pytest

from execution_service.providers.base import OrderRequest
from execution_service.providers.bybit import BybitProvider
from execution_service.providers.ctrader import CTraderProvider


class _FakeCTraderAdapter:
    @property
    def available(self) -> bool:
        return True

    async def place_market_order(self, **kwargs):
        return {"executionPrice": 1.2345}

    async def get_account_info(self):
        return {"balance": 1000, "equity": 1000}

    async def close_position(self, **kwargs):
        return {"executionPrice": 1.2, "volume": 0.01}

    async def get_positions(self):
        return []

    async def get_history(self, **kwargs):
        return []

    async def health_check(self):
        return type("Health", (), {"status": "healthy", "reason": ""})()


@pytest.mark.asyncio
async def test_ctrader_place_order_requires_broker_order_id() -> None:
    provider = CTraderProvider(
        client_id="id",
        client_secret="secret",
        access_token="token",
        refresh_token="refresh",
        account_id=0,
        live=False,
    )
    provider._connected = True
    provider._execution_adapter = _FakeCTraderAdapter()

    result = await provider.place_order(
        OrderRequest(symbol="EURUSD", side="buy", volume=0.01, order_type="market")
    )

    assert result.success is False
    assert "missing_order_id" in str(result.error_message)


class _FakeBybitSession:
    def get_wallet_balance(self, **kwargs):
        return {"retCode": 0, "result": {"list": [{"totalEquity": "1000", "totalAvailableBalance": "800", "coin": []}]}}


@pytest.mark.asyncio
async def test_bybit_health_check_mode_mismatch() -> None:
    provider = BybitProvider(api_key="abc", api_secret="xyz", testnet=True)
    provider.mode = "live"
    provider._connected = True
    provider._session = _FakeBybitSession()

    health = await provider.health_check()

    assert health["status"] == "degraded"
    assert "provider_mode_mismatch" in health["reason"]
