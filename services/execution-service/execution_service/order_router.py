"""Order router — routes orders to the correct broker provider."""
from __future__ import annotations

import logging
from typing import Dict

from .providers.base import BrokerProvider, OrderRequest, OrderResult

logger = logging.getLogger(__name__)


class OrderRouter:
    """
    Routes order requests to the appropriate broker provider.
    Supports multiple named providers (one per bot / broker connection).
    """

    def __init__(self) -> None:
        self._providers: Dict[str, BrokerProvider] = {}

    def register(self, name: str, provider: BrokerProvider) -> None:
        self._providers[name] = provider
        logger.info("Registered provider: %s", name)

    def get(self, name: str) -> BrokerProvider:
        provider = self._providers.get(name)
        if provider is None:
            raise KeyError(f"Provider not found: {name!r}")
        return provider

    async def route(self, provider_name: str, request: OrderRequest) -> OrderResult:
        provider = self.get(provider_name)
        if not provider.is_connected:
            raise RuntimeError(f"Provider {provider_name!r} is not connected")
        logger.info(
            "Routing order: provider=%s symbol=%s side=%s vol=%.2f",
            provider_name, request.symbol, request.side, request.volume,
        )
        return await provider.place_order(request)

    async def close(self, provider_name: str, position_id: str) -> OrderResult:
        provider = self.get(provider_name)
        return await provider.close_position(position_id)
