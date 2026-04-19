"""execution_service.providers — broker provider registry."""

from .base import BrokerProvider, OrderRequest, OrderResult, AccountInfo
from .paper import PaperProvider
from .ctrader import CTraderProvider
from .mt5 import MT5Provider
from .bybit import BybitProvider

__all__ = [
    "BrokerProvider",
    "OrderRequest",
    "OrderResult",
    "AccountInfo",
    "PaperProvider",
    "CTraderProvider",
    "MT5Provider",
    "BybitProvider",
]


def get_provider(provider_type: str, **kwargs) -> BrokerProvider:
    """Factory function to instantiate a provider by type string."""
    registry = {
        "paper": PaperProvider,
        "ctrader": CTraderProvider,
        "mt5": MT5Provider,
        "bybit": BybitProvider,
    }
    cls = registry.get(provider_type)
    if cls is None:
        raise ValueError(f"Unknown provider type: {provider_type!r}")
    return cls(**kwargs)
