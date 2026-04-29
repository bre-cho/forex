from __future__ import annotations

import pandas as pd

from trading_core.data.market_data_quality import MarketDataQualityEngine


def _frame(rows: list[dict], freq: str = "5min"):
    idx = pd.date_range("2026-01-01", periods=len(rows), freq=freq)
    df = pd.DataFrame(rows, index=idx)
    return df


def test_market_data_quality_detects_duplicate_timestamp() -> None:
    df = _frame(
        [
            {"open": 1.1, "high": 1.2, "low": 1.0, "close": 1.15},
            {"open": 1.15, "high": 1.25, "low": 1.1, "close": 1.2},
        ]
    )
    df = pd.concat([df.iloc[[0]], df.iloc[[0]], df.iloc[[1]]])

    result = MarketDataQualityEngine().evaluate(df)
    assert result.ok is False
    assert result.reason == "duplicate_timestamp"


def test_market_data_quality_detects_invalid_ohlc() -> None:
    df = _frame(
        [
            {"open": 1.1, "high": 1.0, "low": 1.2, "close": 1.15},
            {"open": 1.15, "high": 1.25, "low": 1.1, "close": 1.2},
        ]
    )

    result = MarketDataQualityEngine().evaluate(df)
    assert result.ok is False
    assert result.reason == "invalid_ohlc_values"


def test_market_data_quality_passes_valid_frame() -> None:
    df = _frame(
        [
            {"open": 1.1, "high": 1.2, "low": 1.0, "close": 1.15, "bid": 1.1499, "ask": 1.1501},
            {"open": 1.15, "high": 1.25, "low": 1.1, "close": 1.2, "bid": 1.1999, "ask": 1.2002},
            {"open": 1.2, "high": 1.3, "low": 1.15, "close": 1.28, "bid": 1.2799, "ask": 1.2802},
        ]
    )

    result = MarketDataQualityEngine(max_spread_pips=5.0).evaluate(df)
    assert result.ok is True
    assert result.reason == "ok"
