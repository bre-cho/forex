from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from .broker_native_risk_context import CurrencyConversionService
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
    def _infer_quote_currency(symbol: str) -> str:
        s = str(symbol or "").upper()
        if len(s) >= 6 and s[:3].isalpha() and s[3:6].isalpha():
            return s[3:6]
        if s.endswith("USDT"):
            return "USDT"
        if s.endswith("USD"):
            return "USD"
        return "USD"

    @staticmethod
    def _default_correlation_buckets() -> dict[str, list[str]]:
        return {
            "USD": ["USD", "USDT"],
            "EUR": ["EUR"],
            "JPY": ["JPY"],
            "GBP": ["GBP"],
            "XAU": ["XAU", "XAG"],
            "CRYPTO": ["BTC", "ETH", "USDT"],
        }

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
        instrument_specs_by_symbol: Mapping[str, InstrumentSpec] | None = None,
        quote_snapshots: Mapping[str, Mapping[str, Any]] | None = None,
        conversion_rates: Mapping[str, float] | None = None,
        correlation_buckets: Mapping[str, list[str]] | None = None,
        slippage_pips: float = 0.0,
        commission_per_lot: float | None = None,
    ) -> RiskContext:
        equity = float(getattr(account_info, "equity", 0.0) or 0.0)
        free_margin = float(getattr(account_info, "free_margin", 0.0) or 0.0)
        margin = float(getattr(account_info, "margin", 0.0) or 0.0)
        account_currency = str(getattr(account_info, "currency", "") or "").upper() or "USD"

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
        quote_currency = str((spec.quote_currency if spec else RiskContextBuilder._infer_quote_currency(symbol)) or "USD").upper()

        conversion = CurrencyConversionService(
            quote_snapshots=quote_snapshots,
            static_rates=conversion_rates,
            runtime_mode=runtime_mode,
        )
        bucket_config = {
            str(k).upper(): [str(t).upper() for t in (v or [])]
            for k, v in dict(correlation_buckets or RiskContextBuilder._default_correlation_buckets()).items()
        }

        volume = abs(float(requested_volume or 0.0))
        price = float(entry_price or 0.0)
        notional = volume * contract_size * price
        notional_account = conversion.convert_amount(
            amount=notional,
            from_currency=quote_currency,
            to_currency=account_currency,
        )

        current_notional_account = 0.0
        symbol_notional_account = 0.0
        bucket_notional_account: dict[str, float] = {k: 0.0 for k in bucket_config.keys()}

        def _add_buckets(amount_account: float, symbol_name: str, quote_ccy: str, base_ccy: str | None) -> None:
            tags = {str(symbol_name or "").upper(), str(quote_ccy or "").upper(), str(base_ccy or "").upper()}
            for bucket, tokens in bucket_config.items():
                if any(t in tags for t in tokens):
                    bucket_notional_account[bucket] = float(bucket_notional_account.get(bucket, 0.0) + amount_account)

        _add_buckets(notional_account, symbol, quote_currency, getattr(spec, "base_currency", None) if spec else None)

        for pos in open_positions or []:
            pos_symbol = str(pos.get("symbol") or "").upper()
            v = abs(float(pos.get("volume") or pos.get("qty") or 0.0))
            p = float(pos.get("open_price") or pos.get("price") or price or 0.0)
            pos_spec = None
            if instrument_specs_by_symbol:
                pos_spec = instrument_specs_by_symbol.get(pos_symbol)
            if pos_spec is None and pos_symbol == str(symbol or "").upper():
                pos_spec = spec
            if pos_spec is None and runtime_mode != "live":
                pos_spec = get_fallback_spec(pos_symbol)
            if pos_spec is None:
                raise RuntimeError(f"risk_context_missing_position_spec:{pos_symbol}")

            pos_quote = str(pos_spec.quote_currency or RiskContextBuilder._infer_quote_currency(pos_symbol)).upper()
            pos_contract_size = float(pos_spec.contract_size or contract_size)
            n = v * pos_contract_size * p
            n_account = conversion.convert_amount(
                amount=n,
                from_currency=pos_quote,
                to_currency=account_currency,
            )
            current_notional_account += n_account
            if pos_symbol == str(symbol or "").upper():
                symbol_notional_account += n_account
            _add_buckets(n_account, pos_symbol, pos_quote, pos_spec.base_currency)

        total_notional = current_notional_account + notional_account
        account_exposure = (total_notional / equity) * 100.0
        symbol_exposure = ((symbol_notional_account + notional_account) / equity) * 100.0

        sl = float(stop_loss or 0.0)
        pip_size = spec.pip_size if spec else pip_size_for_symbol(symbol)
        pip_value_quote = spec.pip_value_per_lot(price) if spec else pip_value_per_lot(symbol)
        pip_value = conversion.convert_amount(
            amount=float(pip_value_quote or 0.0),
            from_currency=quote_currency,
            to_currency=account_currency,
        )
        if runtime_mode == "live" and float(pip_value or 0.0) <= 0:
            raise RuntimeError("risk_context_pip_value_unavailable")
        stop_pips = abs(price - sl) / pip_size if sl > 0 and price > 0 else 0.0
        price_sl_loss = stop_pips * pip_value * volume
        slippage_loss = max(0.0, float(slippage_pips or 0.0)) * pip_value * volume
        commission_cost = float(commission_per_lot if commission_per_lot is not None else getattr(spec, "commission_per_lot", 0.0) or 0.0) * volume
        commission_cost_account = conversion.convert_amount(
            amount=commission_cost,
            from_currency=quote_currency,
            to_currency=account_currency,
        )
        max_loss = price_sl_loss + slippage_loss + commission_cost_account

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

        # Backward-compatible field: keep USD bucket output.
        correlated_usd = float(bucket_notional_account.get("USD", 0.0))
        correlated = (correlated_usd / equity) * 100.0 if equity > 0 else 0.0

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
