from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .pip_value import pip_size_for_symbol, pip_value_per_lot


@dataclass(frozen=True)
class RiskContext:
    margin_usage_pct: float
    free_margin_after_order: float
    account_exposure_pct: float
    symbol_exposure_pct: float
    correlated_usd_exposure_pct: float
    pip_value_account_currency: float
    max_loss_amount_if_sl_hit: float
    risk_per_trade_pct: float


class RiskContextBuilder:
    @staticmethod
    def build(
        *,
        account_info: Any,
        open_positions: list[dict],
        symbol: str,
        entry_price: float,
        stop_loss: float | None,
        requested_volume: float,
        risk_pct: float,
    ) -> RiskContext:
        equity = float(getattr(account_info, "equity", 0.0) or 0.0)
        free_margin = float(getattr(account_info, "free_margin", 0.0) or 0.0)
        margin = float(getattr(account_info, "margin", 0.0) or 0.0)

        if equity <= 0:
            raise RuntimeError("risk_context_missing_equity")

        notional = abs(float(requested_volume or 0.0) * float(entry_price or 0.0) * 100000.0)
        current_notional = 0.0
        symbol_notional = 0.0
        for pos in open_positions or []:
            v = float(pos.get("volume") or pos.get("qty") or 0.0)
            p = float(pos.get("open_price") or pos.get("price") or entry_price or 0.0)
            n = abs(v * p * 100000.0)
            current_notional += n
            if str(pos.get("symbol") or "").upper() == str(symbol or "").upper():
                symbol_notional += n

        total_notional = current_notional + notional
        account_exposure = (total_notional / equity) * 100.0
        symbol_exposure = ((symbol_notional + notional) / equity) * 100.0

        sl = float(stop_loss or 0.0)
        pip_size = pip_size_for_symbol(symbol)
        pip_value = pip_value_per_lot(symbol)
        stop_pips = abs(float(entry_price or 0.0) - sl) / pip_size if sl > 0 and entry_price > 0 else 0.0
        max_loss = stop_pips * pip_value * float(requested_volume or 0.0)

        projected_margin = margin + notional * 0.01
        margin_usage_pct = (projected_margin / equity) * 100.0
        free_margin_after = free_margin - notional * 0.01

        # conservative proxy for correlation bucket (USD-quoted majors grouped)
        correlated = 0.0
        if "USD" in str(symbol or "").upper():
            correlated = account_exposure

        return RiskContext(
            margin_usage_pct=margin_usage_pct,
            free_margin_after_order=free_margin_after,
            account_exposure_pct=account_exposure,
            symbol_exposure_pct=symbol_exposure,
            correlated_usd_exposure_pct=correlated,
            pip_value_account_currency=pip_value,
            max_loss_amount_if_sl_hit=max_loss,
            risk_per_trade_pct=float(risk_pct or 0.0),
        )
