from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class QualityResult:
    ok: bool
    reason: str = "ok"
    details: dict[str, Any] = field(default_factory=dict)


class MarketDataQualityEngine:
    def __init__(self, *, max_gap_multiplier: float = 2.2, max_spread_pips: float = 10.0) -> None:
        self.max_gap_multiplier = float(max_gap_multiplier)
        self.max_spread_pips = float(max_spread_pips)

    def evaluate(self, df) -> QualityResult:
        if df is None:
            return QualityResult(False, "missing_dataframe")
        if not hasattr(df, "empty") or df.empty:
            return QualityResult(False, "empty_dataframe")

        required = {"open", "high", "low", "close"}
        raw_columns = getattr(df, "columns", None)
        columns = set(list(raw_columns)) if raw_columns is not None else set()
        if not required.issubset(columns):
            return QualityResult(False, "missing_ohlc_columns", {"columns": sorted(list(columns))})

        idx = getattr(df, "index", None)
        if idx is None or len(idx) < 2:
            return QualityResult(True, "ok")

        if bool(idx.has_duplicates):
            return QualityResult(False, "duplicate_timestamp")

        gaps = idx.to_series().diff().dropna()
        if not gaps.empty:
            median_gap = gaps.median()
            if median_gap.total_seconds() > 0:
                max_gap = gaps.max()
                if max_gap.total_seconds() > median_gap.total_seconds() * self.max_gap_multiplier:
                    return QualityResult(
                        False,
                        "candle_gap_detected",
                        {
                            "median_gap_seconds": median_gap.total_seconds(),
                            "max_gap_seconds": max_gap.total_seconds(),
                        },
                    )

        invalid_ohlc = bool(((df["high"] < df["low"]) | (df["open"] <= 0) | (df["close"] <= 0)).any())
        if invalid_ohlc:
            return QualityResult(False, "invalid_ohlc_values")

        if "bid" in columns and "ask" in columns:
            spread = (df["ask"] - df["bid"]).astype(float)
            if (spread < 0).any():
                return QualityResult(False, "negative_spread")
            spread_pips = spread * 10000.0
            if float(spread_pips.max()) > self.max_spread_pips:
                return QualityResult(
                    False,
                    "abnormal_spread",
                    {"max_spread_pips": float(spread_pips.max())},
                )

        return QualityResult(True, "ok")
