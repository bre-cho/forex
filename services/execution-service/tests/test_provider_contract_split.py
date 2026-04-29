from __future__ import annotations

import pytest

from execution_service.providers import get_provider
from execution_service.providers.ctrader import CTraderProvider
from execution_service.providers.ctrader_demo import CTraderDemoProvider
from execution_service.providers.ctrader_live import CTraderLiveProvider
from execution_service.providers.mt5 import MT5Provider
from execution_service.providers.mt5_demo import MT5DemoProvider
from execution_service.providers.mt5_live import MT5LiveProvider
from execution_service.providers.bybit import BybitProvider
from execution_service.providers.bybit_demo import BybitDemoProvider
from execution_service.providers.bybit_live import BybitLiveProvider


def _ctrader_args() -> dict:
    return {
        "client_id": "id",
        "client_secret": "secret",
        "access_token": "token",
        "refresh_token": "refresh",
        "account_id": 123,
        "symbol": "EURUSD",
        "timeframe": "M5",
    }


def _mt5_args() -> dict:
    return {
        "login": 123456,
        "password": "secret",
        "server": "demo-server",
        "symbol": "EURUSD",
        "timeframe": "M5",
    }


def _bybit_args() -> dict:
    return {
        "api_key": "k",
        "api_secret": "s",
        "symbol": "BTCUSDT",
        "timeframe": "M5",
    }


def test_ctrader_provider_rejects_live_flag() -> None:
    with pytest.raises(ValueError, match="demo-only"):
        CTraderProvider(**_ctrader_args(), live=True)


def test_ctrader_live_provider_enables_live_mode() -> None:
    provider = CTraderLiveProvider(**_ctrader_args())
    assert provider.live is True


def test_provider_registry_maps_ctrader_to_demo() -> None:
    provider = get_provider("ctrader", **_ctrader_args())
    assert isinstance(provider, CTraderDemoProvider)


def test_provider_registry_supports_explicit_ctrader_live() -> None:
    provider = get_provider("ctrader_live", **_ctrader_args())
    assert isinstance(provider, CTraderLiveProvider)


def test_mt5_provider_rejects_live_flag() -> None:
    with pytest.raises(ValueError, match="demo-only"):
        MT5Provider(**_mt5_args(), live=True)


def test_mt5_live_provider_enables_live_mode() -> None:
    provider = MT5LiveProvider(**_mt5_args())
    assert provider.live is True
    assert provider.mode == "live"


def test_provider_registry_maps_mt5_to_demo() -> None:
    provider = get_provider("mt5", **_mt5_args())
    assert isinstance(provider, MT5DemoProvider)


def test_provider_registry_supports_explicit_mt5_live() -> None:
    provider = get_provider("mt5_live", **_mt5_args())
    assert isinstance(provider, MT5LiveProvider)


def test_bybit_provider_rejects_live_mode() -> None:
    with pytest.raises(ValueError, match="demo-only"):
        BybitProvider(**_bybit_args(), testnet=False)


def test_bybit_live_provider_enables_live_mode() -> None:
    provider = BybitLiveProvider(**_bybit_args())
    assert provider.testnet is False
    assert provider.mode == "live"


def test_provider_registry_maps_bybit_to_demo() -> None:
    provider = get_provider("bybit", **_bybit_args())
    assert isinstance(provider, BybitDemoProvider)


def test_provider_registry_supports_explicit_bybit_live() -> None:
    provider = get_provider("bybit_live", **_bybit_args())
    assert isinstance(provider, BybitLiveProvider)
