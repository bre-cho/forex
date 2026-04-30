from __future__ import annotations

from typing import Any, Mapping


def _normalize_ccy(value: str | None) -> str:
    return str(value or "").strip().upper()


def _mid_from_quote(payload: Mapping[str, Any]) -> float:
    bid = float(payload.get("bid") or 0.0)
    ask = float(payload.get("ask") or 0.0)
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    return float(payload.get("mid") or payload.get("price") or 0.0)


class CurrencyConversionService:
    """Converts amounts across currencies using broker quote snapshots.

    In live mode, conversion is fail-closed when a required FX quote is missing.
    """

    def __init__(
        self,
        *,
        quote_snapshots: Mapping[str, Mapping[str, Any]] | None = None,
        static_rates: Mapping[str, float] | None = None,
        runtime_mode: str = "paper",
    ) -> None:
        self._quotes = dict(quote_snapshots or {})
        self._static_rates = {
            str(k).upper(): float(v)
            for k, v in dict(static_rates or {}).items()
            if float(v or 0.0) > 0
        }
        self._runtime_mode = str(runtime_mode or "paper").lower()

    def _rate_from_quotes(self, from_ccy: str, to_ccy: str) -> float | None:
        pair = f"{from_ccy}{to_ccy}"
        inverse = f"{to_ccy}{from_ccy}"
        if pair in self._quotes:
            mid = _mid_from_quote(self._quotes[pair])
            return mid if mid > 0 else None
        if inverse in self._quotes:
            mid = _mid_from_quote(self._quotes[inverse])
            if mid > 0:
                return 1.0 / mid
        return None

    def get_rate(self, *, from_currency: str, to_currency: str) -> float:
        from_ccy = _normalize_ccy(from_currency)
        to_ccy = _normalize_ccy(to_currency)
        if not from_ccy or not to_ccy:
            raise RuntimeError("risk_context_currency_code_invalid")
        if from_ccy == to_ccy:
            return 1.0

        # Stablecoin parity fallback for broker accounts quoted as USDT.
        if {from_ccy, to_ccy} == {"USD", "USDT"}:
            return 1.0

        static_key = f"{from_ccy}->{to_ccy}"
        if static_key in self._static_rates:
            return float(self._static_rates[static_key])

        quoted = self._rate_from_quotes(from_ccy, to_ccy)
        if quoted is not None and quoted > 0:
            return quoted

        if self._runtime_mode == "live":
            raise RuntimeError(f"risk_context_missing_conversion_rate:{from_ccy}:{to_ccy}")
        return 1.0

    def convert_amount(self, *, amount: float, from_currency: str, to_currency: str) -> float:
        return float(amount or 0.0) * self.get_rate(
            from_currency=from_currency,
            to_currency=to_currency,
        )


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
