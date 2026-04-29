"""InstrumentSpec — broker/symbol specifications for production risk calculations.

This replaces hardcoded assumptions like ``notional * 0.01`` or ``contract_size=100000``
with actual broker-provided data.  Providers must implement ``get_instrument_spec()``
before being approved for live trading.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class InstrumentSpec:
    """Per-symbol instrument specification from a broker.

    All values must come from the broker's instrument/symbol info API.
    None values mean the broker did not provide them — callers must
    treat this as an incomplete spec and block live trading.
    """
    symbol: str
    asset_class: str                # "forex" | "crypto" | "metal" | "index" | "cfd"
    contract_size: float            # e.g. 100000 for EURUSD, 1 for BTCUSDT
    pip_size: float                 # e.g. 0.0001 for EURUSD, 0.01 for USDJPY
    tick_size: float                # minimum price movement
    min_volume: float               # minimum lot/qty
    max_volume: float               # maximum lot/qty
    volume_step: float              # lot step size
    margin_rate: float              # fraction of notional required as margin (e.g. 0.01 = 1%)
    quote_currency: str             # e.g. "USD" for EURUSD
    base_currency: Optional[str]    # e.g. "EUR" for EURUSD, None for indices
    tick_value: Optional[float] = None  # value of 1 tick per 1 lot in quote currency
    account_currency: str = "USD"   # account denomination

    def is_complete(self) -> bool:
        """True if all required fields for live margin calculation are present."""
        return (
            self.contract_size > 0
            and self.margin_rate > 0
            and bool(self.quote_currency)
            and self.pip_size > 0
        )

    def estimated_margin(self, volume: float, price: float) -> float:
        """Estimate required margin in quote currency units.

        This is a best-effort calculation; providers should override with
        ``estimate_margin()`` for accuracy.
        """
        notional = volume * self.contract_size * price
        return notional * self.margin_rate

    def pip_value_per_lot(self, price: float = 1.0) -> float:
        """Value of 1 pip per 1 lot in quote currency."""
        if self.tick_value is not None and self.tick_size > 0:
            return self.tick_value * (self.pip_size / self.tick_size)
        # fallback geometric formula for FX
        return self.pip_size * self.contract_size


# ---------------------------------------------------------------------------
# Well-known FX defaults (fallback only — live must use actual broker spec)
# ---------------------------------------------------------------------------

_FX_DEFAULTS: dict[str, InstrumentSpec] = {
    "EURUSD": InstrumentSpec("EURUSD", "forex", 100000, 0.0001, 0.00001, 0.01, 500.0, 0.01, 0.01, "USD", "EUR"),
    "USDJPY": InstrumentSpec("USDJPY", "forex", 100000, 0.01,   0.001,   0.01, 500.0, 0.01, 0.01, "JPY", "USD"),
    "GBPUSD": InstrumentSpec("GBPUSD", "forex", 100000, 0.0001, 0.00001, 0.01, 500.0, 0.01, 0.01, "USD", "GBP"),
    "XAUUSD": InstrumentSpec("XAUUSD", "metal", 100,    0.01,   0.01,    0.01, 50.0,  0.01, 0.01, "USD", "XAU"),
    "BTCUSDT": InstrumentSpec("BTCUSDT", "crypto", 1,   1.0,    0.1,     0.001, 100.0, 0.001, 0.1, "USDT", "BTC"),
}


def get_fallback_spec(symbol: str) -> Optional[InstrumentSpec]:
    """Return a well-known default spec (for paper/backtest only).

    Returns None if symbol is unknown — callers in live mode must NOT use this.
    """
    return _FX_DEFAULTS.get(symbol.upper())
