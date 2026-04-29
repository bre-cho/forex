from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .pip_value import pip_size_for_symbol, pip_value_per_lot
from .instrument_spec import InstrumentSpec, get_fallback_spec


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
        instrument_spec: Optional[InstrumentSpec] = None,
        runtime_mode: str = "paper",
        broker_margin_required: Optional[float] = None,
    ) -> RiskContext:
        equity = float(getattr(account_info, "equity", 0.0) or 0.0)
        free_margin = float(getattr(account_info, "free_margin", 0.0) or 0.0)
        margin = float(getattr(account_info, "margin", 0.0) or 0.0)

        if equity <= 0:
            raise RuntimeError("risk_context_missing_equity")

        # Resolve instrument spec: live mode requires a real broker spec
        spec = instrument_spec
        if spec is None:
            if runtime_mode == "live":
                raise RuntimeError("risk_context_missing_instrument_spec")
            spec = get_fallback_spec(symbol)

        # Compute contract size (broker-provided or fallback)
        contract_size = spec.contract_size if spec else 100000.0
        margin_rate = spec.margin_rate if spec else 0.01

        volume = abs(float(requested_volume or 0.0))
        price = float(entry_price or 0.0)
        notional = volume * contract_size * price

        current_notional = 0.0
        symbol_notional = 0.0
        for pos in open_positions or []:
            v = abs(float(pos.get("volume") or pos.get("qty") or 0.0))
            p = float(pos.get("open_price") or pos.get("price") or price or 0.0)
            pos_contract_size = contract_size  # best approximation
            n = v * pos_contract_size * p
            current_notional += n
            if str(pos.get("symbol") or "").upper() == str(symbol or "").upper():
                symbol_notional += n

        total_notional = current_notional + notional
        account_exposure = (total_notional / equity) * 100.0
        symbol_exposure = ((symbol_notional + notional) / equity) * 100.0

        sl = float(stop_loss or 0.0)
        pip_size = spec.pip_size if spec else pip_size_for_symbol(symbol)
        pip_value = spec.pip_value_per_lot(price) if spec else pip_value_per_lot(symbol)
        if runtime_mode == "live" and float(pip_value or 0.0) <= 0:
            raise RuntimeError("risk_context_pip_value_unavailable")
        stop_pips = abs(price - sl) / pip_size if sl > 0 and price > 0 else 0.0
        max_loss = stop_pips * pip_value * volume

        # Margin calculation: live mode must use broker-native estimate.
        if runtime_mode == "live":
            if broker_margin_required is None or float(broker_margin_required) <= 0:
                raise RuntimeError("risk_context_missing_broker_margin_estimate")
            margin_required = float(broker_margin_required)
        else:
            margin_required = notional * margin_rate

        projected_margin = margin + margin_required
        margin_usage_pct = (projected_margin / equity) * 100.0
        free_margin_after = free_margin - margin_required

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
