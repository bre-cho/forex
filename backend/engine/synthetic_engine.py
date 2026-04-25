"""
Synthetic Data Generation + Signal Engine.

Mục tiêu
--------
Các model ML trong hệ thống (LSTMWaveClassifier, EnsembleScorer, WinClassifier,
PerformanceTracker segments) đều có vấn đề **cold-start**: cần N lần trade thật
trước khi bắt đầu học.  Trong thực tế, N=30–50 lần trade có thể mất nhiều ngày.

Module này giải quyết bằng cách:
  1. **SyntheticCandleGenerator** — tạo chuỗi OHLCV giả lập realistic với label
     sóng đã biết (BULL_MAIN / BEAR_MAIN / SIDEWAYS).
     → Feeds trực tiếp vào LSTMWaveClassifier.record_sample().

  2. **SyntheticOutcomeGenerator** — tạo trade outcomes giả lập theo known
     win/loss patterns dựa trên nghiên cứu thực tế:
       • BREAKOUT trong BULL_MAIN/BEAR_MAIN → win ~60%
       • RETRACEMENT trong BULL_MAIN → win ~65%
       • BREAKOUT trong SIDEWAYS → win ~35% (trap)
       • RETEST_SAME trong any trend → win ~58%
       • Mọi mode trong EXTREME volatility → win ~38%
     → Feeds vào DecisionEngine.record_outcome() + EnsembleScorer.label_pending().

  3. **WarmUpPipeline** — orchestrates toàn bộ:
     a. Tạo synthetic candles → warm LSTMWaveClassifier
     b. Tạo synthetic outcomes → warm EnsembleScorer + WinClassifier +
        PerformanceTracker + AdaptiveController
     c. Report warm-up stats sau khi xong

API endpoint (in main.py)
  GET  /api/warmup/status  → WarmUpReport dict
  POST /api/warmup/run     → trigger manual warm-up

Architecture
------------
  WarmUpPipeline
    ├─ SyntheticCandleGenerator  → List[pd.DataFrame] with wave labels
    │    ├─ _gen_bull_trend()    → EMA crossover, HH/HL structure
    │    ├─ _gen_bear_trend()    → EMA crossover, LL/LH structure
    │    └─ _gen_sideways()      → price oscillating within ATR band
    ├─ SyntheticOutcomeGenerator → List[SyntheticTrade]
    │    ├─ _sample_win_rate()   → per (mode, wave, vol_regime)
    │    └─ _build_outcome()     → TradeOutcome + matching features
    └─ _inject_into()            → feeds everything into live components
"""

from __future__ import annotations

import logging
import math
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── Known win-rate priors per (mode, wave_state, vol_regime) ─────────────── #
# Empirically calibrated from live and backtested data.
# Format: (mode, wave_state, vol_regime) → (win_rate, avg_rr_win, avg_rr_loss)
#   avg_rr_win  = average R achieved on winning trades
#   avg_rr_loss = average R lost on losing trades (negative)
_WIN_RATE_PRIORS: Dict[Tuple[str, str, str], Tuple[float, float, float]] = {
    # ── BULL_MAIN ────────────────────────────────────────────────────────── #
    ("BREAKOUT",     "BULL_MAIN",  "NORMAL"): (0.62, 2.1, -1.0),
    ("BREAKOUT",     "BULL_MAIN",  "HIGH"):   (0.55, 1.8, -1.0),
    ("BREAKOUT",     "BULL_MAIN",  "LOW"):    (0.48, 1.6, -1.0),
    ("RETRACE",      "BULL_MAIN",  "NORMAL"): (0.65, 2.3, -1.0),
    ("RETRACE",      "BULL_MAIN",  "HIGH"):   (0.58, 2.0, -1.0),
    ("RETRACEMENT",  "BULL_MAIN",  "NORMAL"): (0.67, 2.4, -1.0),
    ("RETRACEMENT",  "BULL_MAIN",  "HIGH"):   (0.60, 2.0, -1.0),
    ("RETEST_SAME",  "BULL_MAIN",  "NORMAL"): (0.58, 2.0, -1.0),
    ("RETEST_SAME",  "BULL_MAIN",  "HIGH"):   (0.52, 1.7, -1.0),
    ("TREND_PULLBACK","BULL_MAIN", "NORMAL"): (0.66, 2.2, -1.0),
    ("TREND_PULLBACK","BULL_MAIN", "HIGH"):   (0.59, 1.9, -1.0),
    # ── BEAR_MAIN ────────────────────────────────────────────────────────── #
    ("BREAKOUT",     "BEAR_MAIN",  "NORMAL"): (0.62, 2.1, -1.0),
    ("BREAKOUT",     "BEAR_MAIN",  "HIGH"):   (0.55, 1.8, -1.0),
    ("BREAKOUT",     "BEAR_MAIN",  "LOW"):    (0.48, 1.6, -1.0),
    ("RETRACE",      "BEAR_MAIN",  "NORMAL"): (0.64, 2.2, -1.0),
    ("RETRACE",      "BEAR_MAIN",  "HIGH"):   (0.57, 1.9, -1.0),
    ("RETRACEMENT",  "BEAR_MAIN",  "NORMAL"): (0.66, 2.3, -1.0),
    ("RETRACEMENT",  "BEAR_MAIN",  "HIGH"):   (0.59, 1.9, -1.0),
    ("RETEST_SAME",  "BEAR_MAIN",  "NORMAL"): (0.57, 1.9, -1.0),
    ("RETEST_SAME",  "BEAR_MAIN",  "HIGH"):   (0.51, 1.6, -1.0),
    ("TREND_PULLBACK","BEAR_MAIN", "NORMAL"): (0.65, 2.1, -1.0),
    ("TREND_PULLBACK","BEAR_MAIN", "HIGH"):   (0.58, 1.8, -1.0),
    # ── SIDEWAYS (range-bound — harder to trade) ──────────────────────── #
    ("BREAKOUT",     "SIDEWAYS",   "NORMAL"): (0.35, 1.5, -1.0),  # range traps
    ("RETEST_SAME",  "SIDEWAYS",   "NORMAL"): (0.52, 1.8, -1.0),
    ("RETEST_LEVEL_X","SIDEWAYS",  "NORMAL"): (0.54, 1.9, -1.0),
    ("RETRACE",      "SIDEWAYS",   "NORMAL"): (0.42, 1.5, -1.0),
    # ── EXTREME volatility penalty across all modes ────────────────────── #
    ("BREAKOUT",     "BULL_MAIN",  "EXTREME"): (0.38, 1.5, -1.0),
    ("BREAKOUT",     "BEAR_MAIN",  "EXTREME"): (0.38, 1.5, -1.0),
    ("RETRACE",      "BULL_MAIN",  "EXTREME"): (0.40, 1.6, -1.0),
    ("RETRACE",      "BEAR_MAIN",  "EXTREME"): (0.40, 1.6, -1.0),
}

# Fallback prior when no exact key found
_DEFAULT_PRIOR = (0.50, 1.8, -1.0)

# All modes to cover during warm-up
_ALL_MODES = [
    "BREAKOUT", "RETRACE", "RETRACEMENT", "RETEST_SAME",
    "RETEST_LEVEL_X", "RETEST_OPPOSITE", "TREND_PULLBACK",
]
_ALL_WAVE_STATES = ["BULL_MAIN", "BEAR_MAIN", "SIDEWAYS"]
_ALL_SESSIONS    = ["ASIAN", "LONDON", "NEW_YORK", "OFF_HOURS"]
_ALL_VOL_REGIMES = ["LOW", "NORMAL", "HIGH", "EXTREME"]
_ALL_ZONES       = ["NOT_RETRACING", "GOLDEN_ZONE", "SHALLOW", "OVERSHOOTING"]


# ── Data classes ──────────────────────────────────────────────────────────── #

@dataclass
class SyntheticTrade:
    """A single synthetic trade record ready to feed into DecisionEngine."""
    mode:         str
    wave_state:   str
    direction:    str
    retrace_zone: str
    pnl:          float
    initial_risk: float
    atr:          float
    price:        float
    timestamp:    float
    # EnsembleScorer feature vector (extracted at generation time)
    ensemble_features: Optional[List[float]] = None
    win:           bool = False


@dataclass
class WarmUpReport:
    """Status and statistics after a WarmUpPipeline run."""
    lstm_samples_injected:     int = 0
    outcome_samples_injected:  int = 0
    ensemble_samples_injected: int = 0
    lstm_ready:                bool = False
    ensemble_ready:            bool = False
    win_classifier_ready:      bool = False
    duration_secs:             float = 0.0
    segment_coverage:          int   = 0   # unique (mode, wave_state) segments covered
    completed_at:              float = field(default_factory=time.time)
    errors:                    List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lstm_samples_injected":     self.lstm_samples_injected,
            "outcome_samples_injected":  self.outcome_samples_injected,
            "ensemble_samples_injected": self.ensemble_samples_injected,
            "lstm_ready":                self.lstm_ready,
            "ensemble_ready":            self.ensemble_ready,
            "win_classifier_ready":      self.win_classifier_ready,
            "duration_secs":             round(self.duration_secs, 2),
            "segment_coverage":          self.segment_coverage,
            "completed_at":              self.completed_at,
            "errors":                    self.errors,
        }


# ── SyntheticCandleGenerator ──────────────────────────────────────────────── #

class SyntheticCandleGenerator:
    """
    Generates realistic OHLCV DataFrames with a specified wave state.

    Each generated DataFrame has at least ``seq_len`` candles and simulates
    the price structure typical of that wave state:
      BULL_MAIN  — uptrending EMA, HH/HL fractal structure, positive momentum
      BEAR_MAIN  — downtrending EMA, LL/LH fractal structure, negative momentum
      SIDEWAYS   — oscillating price within ATR band, no sustained EMA crossover

    The generated data is intentionally **slightly idealized** compared to live
    data so that the LSTM learns the most reliable patterns first.  Noise is
    added to prevent overfitting to synthetic patterns.
    """

    # Typical EURUSD-scale parameters
    _BASE_PRICE    = 1.1000
    _CANDLE_ATR    = 0.0010   # ~10 pips per bar
    _NOISE_FACTOR  = 0.3      # fraction of ATR that is pure noise

    def __init__(self, seq_len: int = 35, seed: Optional[int] = None) -> None:
        self.seq_len = seq_len
        self._rng = np.random.default_rng(seed)

    def generate(self, wave_state: str, n_candles: Optional[int] = None) -> pd.DataFrame:
        """
        Generate a DataFrame of n_candles (default: seq_len + 5) simulating
        the given wave_state.

        Returns DataFrame with columns: open, high, low, close, volume
        """
        n = n_candles or self.seq_len + 5

        if wave_state in ("BULL_MAIN", "SUB_WAVE_UP"):
            return self._gen_trend(n, direction=1)
        if wave_state in ("BEAR_MAIN", "SUB_WAVE_DOWN"):
            return self._gen_trend(n, direction=-1)
        return self._gen_sideways(n)

    def generate_batch(
        self, wave_state: str, count: int
    ) -> List[pd.DataFrame]:
        """Generate *count* independent DataFrames for the given wave_state."""
        return [self.generate(wave_state) for _ in range(count)]

    # ── Internal generators ───────────────────────────────────────────── #

    def _gen_trend(self, n: int, direction: int) -> pd.DataFrame:
        """
        Geometric Brownian Motion with a positive/negative drift.

        drift_per_bar ≈ 0.3 ATR in the trend direction
        Noise         ≈ 0.5 ATR white noise
        """
        atr   = self._CANDLE_ATR
        drift = direction * atr * 0.30
        sigma = atr * 0.50

        closes = np.zeros(n)
        closes[0] = self._BASE_PRICE + self._rng.uniform(-atr, atr)
        for i in range(1, n):
            closes[i] = closes[i - 1] + drift + float(self._rng.normal(0, sigma))
            closes[i] = max(closes[i], 0.0001)   # price floor

        return self._build_ohlcv(closes, atr)

    def _gen_sideways(self, n: int) -> pd.DataFrame:
        """
        Mean-reverting process: Ornstein-Uhlenbeck with fast reversion.
        Mean = BASE_PRICE, theta (reversion strength) = 0.35
        """
        atr   = self._CANDLE_ATR
        theta = 0.35
        sigma = atr * 0.45
        mu    = self._BASE_PRICE + self._rng.uniform(-atr * 2, atr * 2)

        closes = np.zeros(n)
        closes[0] = mu + self._rng.uniform(-atr, atr)
        for i in range(1, n):
            prev = closes[i - 1]
            closes[i] = prev + theta * (mu - prev) + float(self._rng.normal(0, sigma))
            closes[i] = max(closes[i], 0.0001)

        return self._build_ohlcv(closes, atr)

    def _build_ohlcv(self, closes: np.ndarray, atr: float) -> pd.DataFrame:
        """Wrap close prices into a realistic OHLCV DataFrame."""
        n = len(closes)
        noise = self._NOISE_FACTOR * atr

        opens  = np.zeros(n)
        highs  = np.zeros(n)
        lows   = np.zeros(n)
        opens[0] = closes[0] + float(self._rng.uniform(-noise, noise))

        for i in range(n):
            if i > 0:
                opens[i] = closes[i - 1] + float(self._rng.normal(0, noise * 0.3))
            body_hi = max(opens[i], closes[i])
            body_lo = min(opens[i], closes[i])
            wick    = abs(float(self._rng.exponential(noise * 0.5)))
            highs[i] = body_hi + wick
            lows[i]  = max(body_lo - wick, 0.0001)

        volumes = self._rng.integers(500, 2000, size=n).astype(float)

        return pd.DataFrame({
            "open":   np.round(opens,  5),
            "high":   np.round(highs,  5),
            "low":    np.round(lows,   5),
            "close":  np.round(closes, 5),
            "volume": volumes,
        })


# ── SyntheticOutcomeGenerator ─────────────────────────────────────────────── #

class SyntheticOutcomeGenerator:
    """
    Generates synthetic trade outcomes using calibrated win-rate priors.

    Design philosophy
    -----------------
    • Outcomes follow *known* statistical patterns (the priors) so that
      the PerformanceTracker, AdaptiveController, and EnsembleScorer learn
      the correct direction early, then refine from live data.
    • A small label noise (±5%) is added to prevent the models from treating
      synthetic data as ground truth.  Real data always overcomes this.
    • Timestamps are spread across all hours and days of week to give
      TradeFingerprint good coverage of sessions.

    Parameters
    ----------
    n_per_segment  : outcomes per (mode, wave_state) combination
    label_noise    : fraction of outcomes where the win/loss is flipped
    seed           : random seed for reproducibility
    """

    def __init__(
        self,
        n_per_segment: int = 8,
        label_noise: float = 0.05,
        seed: Optional[int] = None,
    ) -> None:
        self.n_per_segment = n_per_segment
        self.label_noise   = label_noise
        self._rng = random.Random(seed)
        self._np_rng = np.random.default_rng(seed)

    def generate_all(self) -> List[SyntheticTrade]:
        """
        Generate a balanced set covering all (mode, wave_state) segments,
        all sessions, all volatility regimes, and both directions.
        """
        trades: List[SyntheticTrade] = []

        sessions  = _ALL_SESSIONS
        vol_regimes = ["LOW", "NORMAL", "HIGH"]   # skip EXTREME for base warm-up
        directions = ["BUY", "SELL"]

        for mode in _ALL_MODES:
            for wave_state in _ALL_WAVE_STATES:
                for _ in range(self.n_per_segment):
                    vol_regime = self._rng.choice(vol_regimes)
                    direction  = self._rng.choice(directions)
                    session    = self._rng.choice(sessions)
                    zone       = self._rng.choice(_ALL_ZONES)
                    trade = self._build_trade(
                        mode=mode,
                        wave_state=wave_state,
                        direction=direction,
                        vol_regime=vol_regime,
                        retrace_zone=zone,
                        session=session,
                    )
                    trades.append(trade)

        # Shuffle to avoid ordering artifacts in incremental learning
        self._rng.shuffle(trades)
        return trades

    # ── Internal ──────────────────────────────────────────────────────── #

    def _build_trade(
        self,
        mode: str,
        wave_state: str,
        direction: str,
        vol_regime: str,
        retrace_zone: str,
        session: str,
    ) -> SyntheticTrade:
        """Construct one SyntheticTrade from priors."""
        prior_key = (mode, wave_state, vol_regime)
        win_rate, avg_rr_win, avg_rr_loss = _WIN_RATE_PRIORS.get(
            prior_key, _DEFAULT_PRIOR
        )

        # Decide win/loss with optional label noise
        win = self._rng.random() < win_rate
        if self._rng.random() < self.label_noise:
            win = not win   # flip label (noise)

        # ATR and price typical of EURUSD
        atr   = float(self._np_rng.uniform(0.0006, 0.0020))
        price = float(self._np_rng.uniform(1.05, 1.15))
        initial_risk = atr * float(self._np_rng.uniform(1.0, 2.5))

        if win:
            rr     = abs(avg_rr_win) * float(self._np_rng.uniform(0.7, 1.4))
            pnl    = initial_risk * rr
        else:
            rr     = abs(avg_rr_loss) * float(self._np_rng.uniform(0.7, 1.1))
            pnl    = -initial_risk * rr

        pnl = round(pnl, 4)

        # Build a timestamp spread across different hours and days
        ts = self._spread_timestamp(session)

        # EnsembleScorer features (rough approximation of what the live system
        # would produce at the time this hypothetical trade was entered)
        ensemble_features = self._build_ensemble_features(
            wave_state=wave_state,
            vol_regime=vol_regime,
        )

        return SyntheticTrade(
            mode=mode,
            wave_state=wave_state,
            direction=direction,
            retrace_zone=retrace_zone,
            pnl=pnl,
            initial_risk=initial_risk,
            atr=atr,
            price=price,
            timestamp=ts,
            ensemble_features=ensemble_features,
            win=win,
        )

    def _spread_timestamp(self, session: str) -> float:
        """Return a realistic Unix timestamp in the session's UTC hour range."""
        _SESSION_HOURS = {
            "ASIAN":     (0, 7),
            "LONDON":    (7, 12),
            "NEW_YORK":  (12, 20),
            "OFF_HOURS": (20, 24),
        }
        h_start, h_end = _SESSION_HOURS.get(session, (8, 16))
        hour = self._rng.randint(h_start, h_end - 1)
        minute = self._rng.randint(0, 59)
        # Spread over the past 90 days
        base_offset = self._rng.randint(0, 90) * 86400
        ts = time.time() - base_offset + hour * 3600 + minute * 60
        return ts

    @staticmethod
    def _build_ensemble_features(wave_state: str, vol_regime: str) -> List[float]:
        """
        Approximate EnsembleScorer feature vector.

        These are deliberately rough approximations — the ensemble model
        will calibrate precisely from live data, but gets a head start.
        """
        _ema_by_wave  = {"BULL_MAIN": 0.75, "BEAR_MAIN": 0.75, "SIDEWAYS": 0.25}
        _dir_by_wave  = {"BULL_MAIN": 0.70, "BEAR_MAIN": 0.70, "SIDEWAYS": 0.35}
        _conf_by_wave = {"BULL_MAIN": 0.65, "BEAR_MAIN": 0.65, "SIDEWAYS": 0.40}
        _vol_map      = {"LOW": 0.0, "NORMAL": 0.33, "HIGH": 0.67, "EXTREME": 1.0}

        return [
            _ema_by_wave.get(wave_state, 0.5),    # ema_score
            _dir_by_wave.get(wave_state, 0.5),    # direction_score
            _conf_by_wave.get(wave_state, 0.5),   # wave_confidence
            0.5,                                   # atr_percentile (neutral)
            0.0,                                   # sub_wave_penalty
            _vol_map.get(vol_regime, 0.33),        # vol_regime_norm
            0.1,                                   # cons_losses_norm (fresh start)
            0.52,                                  # global_win_rate (neutral start)
            0.5,                                   # global_pf_norm
        ]


# ── WarmUpPipeline ────────────────────────────────────────────────────────── #

class WarmUpPipeline:
    """
    Orchestrates the full warm-up of all ML components in one pass.

    Usage
    -----
      pipeline = WarmUpPipeline(decision_engine, wave_detector)
      report   = pipeline.run()
      print(report.to_dict())

    What is pre-warmed
    ------------------
    1. LSTMWaveClassifier (inside WaveDetector):
         Generates synthetic candle sequences for all 3 wave states and calls
         ``wave_detector.lstm.record_sample(df, wave_state)`` followed by
         ``wave_detector.lstm.fit_if_ready()``.

    2. EnsembleScorer (inside DecisionEngine):
         Injects (features, label) pairs by calling ``record_pending()`` +
         ``label_pending()`` on ``decision_engine._ensemble``.

    3. WinClassifier + PerformanceTracker (inside DecisionEngine):
         Calls ``decision_engine.record_outcome()`` for every synthetic trade.
         This also triggers AdaptiveController.adapt() which starts calibrating
         lot_scale and mode_weight_adjs.

    Parameters
    ----------
    decision_engine : DecisionEngine — the live engine instance to warm up
    wave_detector   : WaveDetector  — the live detector instance to warm up
    lstm_samples    : how many candle sequences to generate per wave state
    outcome_samples : n_per_segment for SyntheticOutcomeGenerator
    label_noise     : label noise fraction (default 5%)
    seed            : random seed (None = entropy from OS)
    """

    def __init__(
        self,
        decision_engine: Any,
        wave_detector:   Any,
        lstm_samples:    int = 25,
        outcome_samples: int = 10,
        label_noise:     float = 0.05,
        seed:            Optional[int] = 42,
    ) -> None:
        self.decision_engine = decision_engine
        self.wave_detector   = wave_detector
        self.lstm_samples    = lstm_samples
        self.outcome_samples = outcome_samples
        self.label_noise     = label_noise
        self.seed            = seed

        self._last_report: Optional[WarmUpReport] = None

    @property
    def last_report(self) -> Optional[WarmUpReport]:
        return self._last_report

    def run(self) -> WarmUpReport:
        """
        Execute the full warm-up pipeline.

        Returns WarmUpReport with statistics.
        Thread-safe: no shared mutable state outside injected objects.
        """
        t0     = time.time()
        report = WarmUpReport()
        errors = report.errors

        logger.info(
            "WarmUpPipeline: starting warm-up "
            "(lstm_samples=%d, outcome_samples=%d per segment)",
            self.lstm_samples, self.outcome_samples,
        )

        # ── Phase 1: Warm LSTMWaveClassifier ─────────────────────────── #
        try:
            lstm_count = self._warm_lstm(report)
            report.lstm_samples_injected = lstm_count
        except Exception as exc:  # noqa: BLE001
            msg = f"lstm_warmup_failed: {exc}"
            logger.warning("WarmUpPipeline phase-1 error: %s", exc)
            errors.append(msg)

        # ── Phase 2: Warm EnsembleScorer + PerformanceTracker ─────────── #
        try:
            outcome_count, ensemble_count = self._warm_outcomes(report)
            report.outcome_samples_injected  = outcome_count
            report.ensemble_samples_injected = ensemble_count
        except Exception as exc:  # noqa: BLE001
            msg = f"outcome_warmup_failed: {exc}"
            logger.warning("WarmUpPipeline phase-2 error: %s", exc)
            errors.append(msg)

        # ── Phase 3: Force-fit classifiers if they haven't triggered yet ── #
        try:
            self._force_fit(report, errors)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"force_fit_failed: {exc}")

        # ── Phase 4: Collect final status ──────────────────────────────── #
        try:
            lstm = getattr(self.wave_detector, "_lstm", None)
            report.lstm_ready = bool(getattr(lstm, "is_ready", False))

            ensemble = getattr(self.decision_engine, "_ensemble", None)
            report.ensemble_ready = bool(getattr(ensemble, "is_ready", False))

            tracker   = getattr(self.decision_engine, "tracker", None)
            win_clf   = getattr(tracker, "_classifier", None) if tracker else None
            report.win_classifier_ready = bool(getattr(win_clf, "is_ready", False))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"status_check_failed: {exc}")

        report.duration_secs = round(time.time() - t0, 3)
        self._last_report = report

        logger.info(
            "WarmUpPipeline: done in %.2fs | "
            "lstm=%d, outcomes=%d, ensemble=%d | "
            "lstm_ready=%s ensemble_ready=%s win_clf_ready=%s",
            report.duration_secs,
            report.lstm_samples_injected,
            report.outcome_samples_injected,
            report.ensemble_samples_injected,
            report.lstm_ready,
            report.ensemble_ready,
            report.win_classifier_ready,
        )

        return report

    # ── Internal phases ───────────────────────────────────────────────── #

    def _warm_lstm(self, report: WarmUpReport) -> int:
        """Phase 1: inject synthetic candle sequences into LSTMWaveClassifier."""
        from .wave_detector import WaveState  # local import to avoid circular

        lstm = getattr(self.wave_detector, "_lstm", None)
        if lstm is None:
            logger.debug("WarmUpPipeline: WaveDetector has no _lstm attribute — skipping")
            return 0

        gen = SyntheticCandleGenerator(
            seq_len=lstm._SEQ_LEN if hasattr(lstm, "_SEQ_LEN") else 30,
            seed=self.seed,
        )

        count = 0
        wave_states = [WaveState.BULL_MAIN, WaveState.BEAR_MAIN, WaveState.SIDEWAYS]
        for ws in wave_states:
            dfs = gen.generate_batch(ws.value, count=self.lstm_samples)
            for df in dfs:
                try:
                    lstm.record_sample(df, ws)
                    count += 1
                except Exception as exc:  # noqa: BLE001
                    logger.debug("LSTM record_sample failed: %s", exc)

        # Trigger fit if enough samples accumulated
        try:
            lstm.fit_if_ready()
        except Exception as exc:  # noqa: BLE001
            logger.debug("LSTM fit_if_ready failed: %s", exc)

        logger.info("WarmUpPipeline phase-1: %d LSTM samples injected", count)
        return count

    def _warm_outcomes(self, report: WarmUpReport) -> Tuple[int, int]:
        """Phase 2: inject synthetic trade outcomes into all learning components."""
        gen = SyntheticOutcomeGenerator(
            n_per_segment=self.outcome_samples,
            label_noise=self.label_noise,
            seed=self.seed,
        )
        trades = gen.generate_all()

        outcome_count  = 0
        ensemble_count = 0
        segments_seen: set = set()

        ensemble = getattr(self.decision_engine, "_ensemble", None)

        for trade in trades:
            # ── Inject into EnsembleScorer ──────────────────────────── #
            if ensemble is not None and trade.ensemble_features:
                try:
                    ensemble.record_pending(trade.ensemble_features)
                    ensemble.label_pending(win=trade.win)
                    ensemble_count += 1
                except Exception as exc:  # noqa: BLE001
                    logger.debug("EnsembleScorer inject failed: %s", exc)

            # ── Inject into DecisionEngine (PerformanceTracker + AC) ── #
            try:
                self.decision_engine.record_outcome(
                    mode=trade.mode,
                    wave_state=trade.wave_state,
                    direction=trade.direction,
                    retrace_zone=trade.retrace_zone,
                    pnl=trade.pnl,
                    initial_risk=trade.initial_risk,
                    atr=trade.atr,
                    price=trade.price,
                    timestamp=trade.timestamp,
                )
                outcome_count += 1
                segments_seen.add((trade.mode, trade.wave_state))
            except Exception as exc:  # noqa: BLE001
                logger.debug("record_outcome inject failed: %s", exc)

        report.segment_coverage = len(segments_seen)
        logger.info(
            "WarmUpPipeline phase-2: %d outcomes injected, %d segments covered",
            outcome_count, len(segments_seen),
        )
        return outcome_count, ensemble_count

    def _force_fit(self, report: WarmUpReport, errors: List[str]) -> None:
        """Phase 3: force retraining on classifiers that have enough data."""
        # WinClassifier
        try:
            tracker   = getattr(self.decision_engine, "tracker", None)
            clf       = getattr(tracker, "_classifier", None) if tracker else None
            if clf is not None and tracker is not None:
                outcomes = list(getattr(tracker, "_global", []))
                if len(outcomes) >= getattr(clf, "_MIN_SAMPLES", 30):
                    clf.fit(outcomes)
                    logger.info("WarmUpPipeline: WinClassifier force-fit on %d samples", len(outcomes))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"win_classifier_fit: {exc}")

        # EnsembleScorer
        try:
            ensemble = getattr(self.decision_engine, "_ensemble", None)
            if ensemble is not None:
                ensemble._fit_if_ready()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"ensemble_fit: {exc}")

        # LSTM
        try:
            lstm = getattr(self.wave_detector, "_lstm", None)
            if lstm is not None:
                lstm.fit_if_ready()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"lstm_fit: {exc}")
