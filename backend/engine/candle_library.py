"""
Candle Library — Persistent sliding-window candle store.

Maintains up to *capacity* candles (default 10 000) per symbol/timeframe.
Provides:
  1. ``update(candles_df)``     — append new candles from any DataFrame
  2. ``get(n)``                 — retrieve last-n candles as DataFrame
  3. ``extract_features(n)``    — return a Numpy feature matrix for ML use
  4. Persistence helpers         — save/load from Parquet (optional)

Feature vector (per candle, 12 columns)
-----------------------------------------
  close_norm, high_low_range, body_pct,
  upper_wick_pct, lower_wick_pct,
  ema8_diff, ema21_diff, ema50_diff,
  atr14_norm, volume_norm,
  hour_sin, hour_cos
"""

from __future__ import annotations

import logging
import time
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_DEFAULT_CAPACITY = 10_000
_MIN_CANDLES      =    30   # minimum bars before features can be extracted


class CandleLibrary:
    """
    Sliding-window candle library.

    Parameters
    ----------
    capacity : int
        Maximum number of candles to keep (oldest are dropped).
    symbol   : str
        Trading symbol (informational, used for log/save filenames).
    timeframe: str
        Candle timeframe string (informational).
    """

    def __init__(
        self,
        capacity:  int = _DEFAULT_CAPACITY,
        symbol:    str = "EURUSD",
        timeframe: str = "M5",
    ) -> None:
        self.capacity  = capacity
        self.symbol    = symbol
        self.timeframe = timeframe

        # Internal deque of row-dicts (open, high, low, close, volume, timestamp)
        self._store: deque = deque(maxlen=capacity)
        self._last_ts: float = 0.0
        self._update_count: int = 0

    # ── Core API ─────────────────────────────────────────────────────────── #

    def update(self, df: pd.DataFrame) -> int:
        """
        Append rows from *df* that are newer than the last stored candle.

        *df* must contain columns: open, high, low, close, volume, timestamp.
        Returns number of new rows appended.
        """
        if df.empty:
            return 0

        required = {"open", "high", "low", "close", "volume", "timestamp"}
        missing = required - set(df.columns)
        if missing:
            logger.warning("CandleLibrary.update: missing columns %s", missing)
            return 0

        new_rows = df[df["timestamp"] > self._last_ts].copy()
        if new_rows.empty:
            return 0

        new_rows = new_rows.sort_values("timestamp")
        for _, row in new_rows.iterrows():
            self._store.append(row.to_dict())

        self._last_ts = float(new_rows["timestamp"].iloc[-1])
        self._update_count += 1
        added = len(new_rows)
        logger.debug(
            "CandleLibrary[%s %s]: +%d candles (total=%d)",
            self.symbol, self.timeframe, added, len(self._store),
        )
        return added

    def get(self, n: Optional[int] = None) -> pd.DataFrame:
        """Return last *n* candles as a DataFrame (all if n is None)."""
        if not self._store:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "timestamp"])
        rows = list(self._store)
        if n is not None:
            rows = rows[-n:]
        return pd.DataFrame(rows).reset_index(drop=True)

    # ── Feature extraction ────────────────────────────────────────────────── #

    def extract_features(self, n: int = _DEFAULT_CAPACITY) -> Optional[np.ndarray]:
        """
        Build a 2-D Numpy feature matrix of shape (rows, 12).

        Returns ``None`` if fewer than *_MIN_CANDLES* are available.
        Feature columns (per candle):
          0  close_norm        — close / close.mean() − 1
          1  high_low_range    — (high−low) / close
          2  body_pct          — abs(close−open) / (high−low + ε)
          3  upper_wick_pct    — (high−max(open,close)) / (high−low + ε)
          4  lower_wick_pct    — (min(open,close)−low) / (high−low + ε)
          5  ema8_diff         — (close − ema8) / close
          6  ema21_diff        — (close − ema21) / close
          7  ema50_diff        — (close − ema50) / close
          8  atr14_norm        — rolling ATR(14) / close
          9  volume_norm       — volume / volume.rolling(20).mean() − 1
          10 hour_sin          — sin(2π × hour / 24)
          11 hour_cos          — cos(2π × hour / 24)
        """
        df = self.get(n)
        if len(df) < _MIN_CANDLES:
            return None

        close  = df["close"].values.astype(np.float64)
        high   = df["high"].values.astype(np.float64)
        low    = df["low"].values.astype(np.float64)
        open_  = df["open"].values.astype(np.float64)
        volume = df["volume"].values.astype(np.float64)
        ts     = df["timestamp"].values

        eps = 1e-12

        # EMA helper
        def _ema(arr: np.ndarray, span: int) -> np.ndarray:
            s = pd.Series(arr)
            return s.ewm(span=span, adjust=False).mean().values

        hl_range = high - low
        body     = np.abs(close - open_)
        up_wick  = high - np.maximum(close, open_)
        lw_wick  = np.minimum(close, open_) - low
        ema8     = _ema(close, 8)
        ema21    = _ema(close, 21)
        ema50    = _ema(close, 50)

        # ATR(14) via Wilder smoothing
        tr = np.maximum(
            hl_range,
            np.maximum(
                np.abs(high - np.roll(close, 1)),
                np.abs(low  - np.roll(close, 1)),
            ),
        )
        tr[0] = hl_range[0]
        atr14 = pd.Series(tr).ewm(alpha=1/14, adjust=False).mean().values

        # Volume normalisation
        vol_mean = pd.Series(volume).rolling(20, min_periods=1).mean().values
        vol_norm = volume / (vol_mean + eps) - 1.0

        # Hour of day (UTC) from timestamp
        hours = (ts // 3600) % 24
        hour_sin = np.sin(2 * np.pi * hours / 24.0)
        hour_cos = np.cos(2 * np.pi * hours / 24.0)

        close_mean = float(close.mean()) or 1.0
        close_norm = close / close_mean - 1.0

        features = np.column_stack([
            close_norm,
            hl_range / (close + eps),
            body / (hl_range + eps),
            up_wick / (hl_range + eps),
            lw_wick / (hl_range + eps),
            (close - ema8)  / (close + eps),
            (close - ema21) / (close + eps),
            (close - ema50) / (close + eps),
            atr14 / (close + eps),
            vol_norm,
            hour_sin,
            hour_cos,
        ]).astype(np.float32)

        return features

    # ── Persistence ───────────────────────────────────────────────────────── #

    def save(self, path: Optional[str] = None) -> str:
        """Save library to Parquet. Returns the file path used."""
        if path is None:
            path = f"/tmp/candle_lib_{self.symbol}_{self.timeframe}.parquet"
        df = self.get()
        if not df.empty:
            df.to_parquet(path, index=False)
            logger.info("CandleLibrary: saved %d candles to %s", len(df), path)
        return path

    def load(self, path: Optional[str] = None) -> int:
        """Load candles from Parquet. Returns number of rows loaded."""
        if path is None:
            path = f"/tmp/candle_lib_{self.symbol}_{self.timeframe}.parquet"
        p = Path(path)
        if not p.exists():
            return 0
        try:
            df = pd.read_parquet(path)
            required = {"open", "high", "low", "close", "volume", "timestamp"}
            if not required.issubset(df.columns):
                return 0
            for _, row in df.iterrows():
                self._store.append(row.to_dict())
            if len(self._store) > 0:
                self._last_ts = max(r["timestamp"] for r in self._store)
            logger.info("CandleLibrary: loaded %d candles from %s", len(df), path)
            return len(df)
        except Exception as exc:  # noqa: BLE001
            logger.warning("CandleLibrary.load failed: %s", exc)
            return 0

    # ── Properties ────────────────────────────────────────────────────────── #

    @property
    def size(self) -> int:
        return len(self._store)

    @property
    def is_ready(self) -> bool:
        return len(self._store) >= _MIN_CANDLES

    @property
    def last_timestamp(self) -> float:
        return self._last_ts

    @property
    def update_count(self) -> int:
        return self._update_count

    def status(self) -> dict:
        return {
            "total_candles":    self.size,
            "capacity":         self.capacity,
            "symbols":          [self.symbol],
            "last_updated":     self._last_ts,
            "realtime_enabled": True,
        }
