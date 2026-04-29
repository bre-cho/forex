from __future__ import annotations

from typing import Any


async def estimate_live_margin_required(*, provider: Any, symbol: str, side: str, volume: float, price: float) -> float:
    """Use broker-native margin estimation in live mode.

    Raises RuntimeError if provider does not expose a reliable estimate in live paths.
    """
    estimate_fn = getattr(provider, "estimate_margin", None)
    if not callable(estimate_fn):
        raise RuntimeError("broker_margin_estimate_unavailable")
    try:
        value = await estimate_fn(symbol=symbol, side=side, volume=volume, price=price)
    except Exception as exc:
        raise RuntimeError(f"broker_margin_estimate_failed:{exc}") from exc
    required = float(value or 0.0)
    if required <= 0:
        raise RuntimeError("broker_margin_estimate_invalid")
    return required
