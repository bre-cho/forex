"""execution_service.providers — broker provider registry."""

from .base import BrokerProvider, OrderRequest, OrderResult, AccountInfo
from .paper import PaperProvider
from .ctrader import CTraderProvider
from .ctrader_demo import CTraderDemoProvider
from .ctrader_live import CTraderLiveProvider
from .mt5 import MT5Provider
from .mt5_demo import MT5DemoProvider
from .mt5_live import MT5LiveProvider
from .bybit import BybitProvider
from .bybit_demo import BybitDemoProvider
from .bybit_live import BybitLiveProvider

__all__ = [
    "BrokerProvider",
    "OrderRequest",
    "OrderResult",
    "AccountInfo",
    "PaperProvider",
    "CTraderProvider",
    "CTraderDemoProvider",
    "CTraderLiveProvider",
    "MT5Provider",
    "MT5DemoProvider",
    "MT5LiveProvider",
    "BybitProvider",
    "BybitDemoProvider",
    "BybitLiveProvider",
]


def get_provider(provider_type: str, **kwargs) -> BrokerProvider:
    """Factory function to instantiate a provider by type string."""
    registry = {
        "paper": PaperProvider,
        "ctrader": CTraderDemoProvider,
        "ctrader_demo": CTraderDemoProvider,
        "ctrader_live": CTraderLiveProvider,
        "mt5": MT5DemoProvider,
        "mt5_demo": MT5DemoProvider,
        "mt5_live": MT5LiveProvider,
        "bybit": BybitDemoProvider,
        "bybit_demo": BybitDemoProvider,
        "bybit_live": BybitLiveProvider,
    }
    cls = registry.get(provider_type)
    if cls is None:
        raise ValueError(f"Unknown provider type: {provider_type!r}")
    return cls(**kwargs)
