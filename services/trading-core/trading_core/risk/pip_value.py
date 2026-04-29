from __future__ import annotations


def pip_size_for_symbol(symbol: str) -> float:
    s = str(symbol or "").upper()
    if "JPY" in s:
        return 0.01
    return 0.0001


def pip_value_per_lot(symbol: str, quote_to_account_rate: float = 1.0) -> float:
    # Forex major default: 1 lot (100k) ~= $10 per pip for USD quote pairs.
    # For non-USD quote, caller can pass quote_to_account_rate conversion.
    return 10.0 * float(quote_to_account_rate or 1.0)
