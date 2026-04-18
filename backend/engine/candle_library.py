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

Implementation note (item G)
-----------------------------
Internally uses a pre-allocated numpy structured array (ring buffer) instead
of a deque of dicts.  ``get()`` creates a DataFrame from a numpy slice, which
avoids the ``pd.DataFrame(list(deque))`` overhead on every tick.
"""

from __future__ import annotations

import logging
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_DEFAULT_CAPACITY = 10_000
_MIN_CANDLES      =    30   # minimum bars before features can be extracted

# Structured dtype for the ring buffer — 6 fields, compact float storage
_RING_DTYPE = np.dtype([
    ("timestamp", "f8"),
    ("open",      "f4"),
    ("high",      "f4"),
    ("low",       "f4"),
    ("close",     "f4"),
    ("volume",    "f4"),
])

_COLS = list(_RING_DTYPE.names)


class CandleLibrary:
    """
    Sliding-window candle library backed by a numpy ring buffer.

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

        # Ring buffer: pre-allocated fixed-size structured numpy array.
        # _size  — number of valid entries (0..capacity)
        # _write — next write position (0..capacity-1, wraps)
        self._buf:   np.ndarray = np.zeros(capacity, dtype=_RING_DTYPE)
        self._size:  int = 0
        self._write: int = 0

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

        new_rows = df[df["timestamp"] > self._last_ts].sort_values("timestamp")
        if new_rows.empty:
            return 0

        added = len(new_rows)
        ts_arr  = new_rows["timestamp"].to_numpy(dtype="f8")
        op_arr  = new_rows["open"].to_numpy(dtype="f4")
        hi_arr  = new_rows["high"].to_numpy(dtype="f4")
        lo_arr  = new_rows["low"].to_numpy(dtype="f4")
        cl_arr  = new_rows["close"].to_numpy(dtype="f4")
        vo_arr  = new_rows["volume"].to_numpy(dtype="f4")

        for i in range(added):
            self._buf[self._write] = (ts_arr[i], op_arr[i], hi_arr[i], lo_arr[i], cl_arr[i], vo_arr[i])
            self._write = (self._write + 1) % self.capacity
            self._size  = min(self._size + 1, self.capacity)

        self._last_ts = float(ts_arr[-1])
        self._update_count += 1
        logger.debug(
            "CandleLibrary[%s %s]: +%d candles (total=%d)",
            self.symbol, self.timeframe, added, self._size,
        )
        return added

    def get(self, n: Optional[int] = None) -> pd.DataFrame:
        """Return last *n* candles as a DataFrame (all if n is None)."""
        if self._size == 0:
            return pd.DataFrame(columns=_COLS)

        count = min(n if n is not None else self._size, self._size)
        arr = self._get_slice(count)

        return pd.DataFrame({
            "timestamp": arr["timestamp"],
            "open":      arr["open"].astype("f8"),
            "high":      arr["high"].astype("f8"),
            "low":       arr["low"].astype("f8"),
            "close":     arr["close"].astype("f8"),
            "volume":    arr["volume"].astype("f8"),
        }).reset_index(drop=True)

    def _get_slice(self, count: int) -> np.ndarray:
        """Return the last *count* entries in chronological order."""
        if self._size < self.capacity:
            # Buffer not yet full — data sits at indices 0..(_size-1)
            start = self._size - count
            return self._buf[start : self._size]

        # Buffer full (ring).  _write points to the *oldest* slot.
        start = (self._write - count) % self.capacity
        if start < self._write:
            return self._buf[start : self._write]
        # Wraps around
        return np.concatenate([self._buf[start:], self._buf[: self._write]])

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
        """
        Save library to Parquet (preferred) or CSV (fallback).
        Returns the file path used.
        """
        if path is None:
            tmp = tempfile.gettempdir()
            path = str(Path(tmp) / f"candle_lib_{self.symbol}_{self.timeframe}.parquet")
        df = self.get()
        if not df.empty:
            try:
                df.to_parquet(path, index=False)
            except ImportError:
                # Parquet engine unavailable — fall back to CSV
                path = path.replace(".parquet", ".csv")
                df.to_csv(path, index=False)
            logger.info("CandleLibrary: saved %d candles to %s", len(df), path)
        return path

    def load(self, path: Optional[str] = None) -> int:
        """
        Load candles from Parquet or CSV.
        Returns number of rows loaded.
        """
        if path is None:
            tmp = tempfile.gettempdir()
            path = str(Path(tmp) / f"candle_lib_{self.symbol}_{self.timeframe}.parquet")
        p = Path(path)
        # Try the canonical path first; if not found, check CSV alternative
        if not p.exists():
            csv_path = Path(str(p).replace(".parquet", ".csv"))
            if csv_path.exists():
                p = csv_path
            else:
                return 0
        try:
            if str(p).endswith(".csv"):
                df = pd.read_csv(str(p))
            else:
                df = pd.read_parquet(str(p))
            required = {"open", "high", "low", "close", "volume", "timestamp"}
            if not required.issubset(df.columns):
                return 0
            count = self.update(df)
            logger.info("CandleLibrary: loaded %d candles from %s", count, p)
            return count
        except Exception as exc:  # noqa: BLE001
            logger.warning("CandleLibrary.load failed: %s", exc)
            return 0

    # ── Properties ────────────────────────────────────────────────────────── #

    @property
    def size(self) -> int:
        return self._size

    @property
    def is_ready(self) -> bool:
        return self._size >= _MIN_CANDLES

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
