"""
Multi-Agent Game Theory + Market Ecosystem Engine.

Mục tiêu
--------
Nâng cấp từ **rational strategic agent** → **strategic ecosystem intelligence**:

  Hệ không chỉ tối ưu utility của bản thân trong chân không,
  mà tối ưu trong môi trường có:
    • đối thủ (other trader algorithms with known behavioral profiles)
    • thuật toán nền tảng (market maker, noise traders, trend followers)
    • market impact (actions of all agents affect prices)

  Mô hình hóa thành **dynamic multi-agent game**:
    - Mỗi agent có strategy profile (genome)
    - Mỗi hành động của agent thay đổi market state
    - Optimal strategy phụ thuộc vào strategies của tất cả agents khác

  Bước chuyển: rational agent → strategic ecosystem intelligence.

Architecture
------------
  GameTheoryEngine
    ├─ Phase 1: Opponent Spawning
    │    └─ SpawnOpponents: N opponents with typed behavioral strategies
    │         OpponentType: MOMENTUM_FOLLOWER | MEAN_REVERTER | NOISE_TRADER
    │                       MARKET_MAKER | TREND_FADER
    │
    ├─ Phase 2: Ecosystem Simulation
    │    └─ EcosystemSimulator: multi-agent bar-by-bar replay
    │         • Our agent + N opponents trade the same market
    │         • MarketImpactModel adjusts prices based on aggregate positions
    │         • EpisodeRecord: per-agent PnL streams + market price history
    │
    ├─ Phase 3: Nash Equilibrium Analysis
    │    └─ NashEquilibriumFinder: iterative best-response
    │         • Given opponent strategies, what is our best response?
    │         • Given our strategy, how do opponents adapt?
    │         • Converge to approximate Nash equilibrium
    │         • NashEquilibrium: strategy_profile + is_approximate + iterations
    │
    ├─ Phase 4: Exploitability Analysis
    │    └─ ExploitabilityScorer: how exploitable is our strategy?
    │         • Per opponent type: can they exploit us systematically?
    │         • exploitability_score ∈ [0, 1]: 0=unexploitable, 1=fully exploitable
    │         • Reveals which opponent types beat us in head-to-head
    │
    ├─ Phase 5: Best Response Genome Selection
    │    └─ Select genome with best performance IN ecosystem (not isolation)
    │         Takes into account: opponents' strategies, market impact, exploitability
    │
    └─ GameTheoryResult
         ├─ best_response_genome: AgentGenome (optimal in ecosystem)
         ├─ nash_equilibrium:    NashEquilibrium
         ├─ exploitability:      Dict[opponent_type → score]
         ├─ ecosystem_pnls:      Dict[agent_id → List[float]]
         ├─ market_impact_stats: MarketImpactStats
         ├─ ecosystem_insights:  List[str]
         └─ apply_to(decision_engine)

Opponent Behavioral Models
--------------------------
  MOMENTUM_FOLLOWER : buys after N consecutive up bars, sells after N down bars.
                      Crowds trend entries, causes momentum fade at extremes.
  MEAN_REVERTER     : fades moves beyond ±k×ATR from rolling mean.
                      Counter-trades breakouts, provides liquidity at extremes.
  NOISE_TRADER      : random entries with no directional bias.
                      Injects random slippage, increases bid-ask spread.
  MARKET_MAKER      : provides both sides, profits from spread.
                      Adjusts quotes based on inventory; adverse-selection risk.
  TREND_FADER       : identifies and fades late-trend entries.
                      Exploits over-crowded momentum strategies.

MarketImpactModel
-----------------
  When many agents enter the same side simultaneously:
    price_adj = crowding_factor × signed_net_volume × atr × impact_coefficient
  Our effective entry/exit price is degraded by this adjustment.
  Impact decays exponentially: impact_{t} = impact_{t-1} × decay_factor.

NashEquilibriumFinder (Iterative Best Response)
------------------------------------------------
  Repeat for max_iterations:
    1. Fix opponents' current strategies
    2. Find OUR best response genome (max ecosystem fitness)
    3. Fix our strategy, adapt each opponent's strategy (behaviour update)
    4. Check convergence: |strategy_profile[t] - strategy_profile[t-1]| < eps
  Result: approximate Nash equilibrium profile.
  Note: exact Nash in continuous strategy spaces requires LP/MIP —
        iterative best response gives a practical approximation.

ExploitabilityScorer
--------------------
  For each opponent type T:
    1. Run 1v1 simulation: our best_response vs N agents of type T
    2. Compute our win_rate and profit_factor in this matchup
    3. exploitability[T] = max(0, 1 - win_rate × pf_norm)
  High exploitability → opponent type T systematically beats us.
  → We should adjust strategy to reduce exploitability.

API endpoints (wired in main.py)
---------------------------------
  POST /api/ecosystem/run     → trigger full ecosystem simulation + game theory
  GET  /api/ecosystem/status  → GameTheoryResult.to_dict()
  GET  /api/ecosystem/nash    → Nash equilibrium details + strategy profiles
  POST /api/ecosystem/apply   → apply best-response genome to live system
"""

from __future__ import annotations

import logging
import math
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .self_play_engine import (
    AgentFitness,
    AgentGenome,
    MarketSimulator,
    _ALL_MODES,
    _LOOKBACK,
)
from .synthetic_engine import SyntheticCandleGenerator, _ALL_WAVE_STATES

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────── #

_DEFAULT_N_OPPONENTS        = 5
_DEFAULT_N_CANDIDATE_GENOMES = 15
_DEFAULT_EPISODES            = 6
_DEFAULT_BARS_PER_EPISODE    = 70
_DEFAULT_NASH_ITERATIONS     = 8
_IMPACT_COEFFICIENT          = 0.15   # price impact per unit of net crowding
_IMPACT_DECAY                = 0.80   # market impact exponential decay per bar
_RISK_PER_TRADE              = 0.01   # 1% risk per trade (fixed fractional)
_EPS                         = 1e-9


# ── OpponentType ─────────────────────────────────────────────────────────── #

class OpponentType(str, Enum):
    MOMENTUM_FOLLOWER = "MOMENTUM_FOLLOWER"  # chase trends
    MEAN_REVERTER     = "MEAN_REVERTER"      # fade extremes
    NOISE_TRADER      = "NOISE_TRADER"       # random entries
    MARKET_MAKER      = "MARKET_MAKER"       # earn spread, manage inventory
    TREND_FADER       = "TREND_FADER"        # exploit late-trend crowding


# ── EcosystemConfig ───────────────────────────────────────────────────────── #

@dataclass
class EcosystemConfig:
    """
    Configuration for the multi-agent ecosystem simulation.

    Parameters
    ----------
    n_opponents          : number of opponent agents in the ecosystem
    opponent_types       : which opponent types to include (default: all 5)
    n_candidate_genomes  : how many of our genomes to evaluate in ecosystem
    episodes             : market episodes per evaluation
    bars_per_episode     : bars per episode
    nash_iterations      : max iterations for Nash equilibrium convergence
    exploitation_rate    : how aggressively opponents adapt (0=static, 1=fully adaptive)
    impact_coefficient   : market impact per unit of net crowd volume
    impact_decay         : market impact decay per bar (0=permanent, 1=immediate)
    """
    n_opponents:         int                    = _DEFAULT_N_OPPONENTS
    opponent_types:      List[str]              = field(
        default_factory=lambda: [t.value for t in OpponentType]
    )
    n_candidate_genomes: int                    = _DEFAULT_N_CANDIDATE_GENOMES
    episodes:            int                    = _DEFAULT_EPISODES
    bars_per_episode:    int                    = _DEFAULT_BARS_PER_EPISODE
    nash_iterations:     int                    = _DEFAULT_NASH_ITERATIONS
    exploitation_rate:   float                  = 0.5
    impact_coefficient:  float                  = _IMPACT_COEFFICIENT
    impact_decay:        float                  = _IMPACT_DECAY

    def to_dict(self) -> Dict[str, Any]:
        return {
            "n_opponents":         self.n_opponents,
            "opponent_types":      self.opponent_types,
            "n_candidate_genomes": self.n_candidate_genomes,
            "episodes":            self.episodes,
            "bars_per_episode":    self.bars_per_episode,
            "nash_iterations":     self.nash_iterations,
            "exploitation_rate":   round(self.exploitation_rate, 3),
            "impact_coefficient":  round(self.impact_coefficient, 4),
            "impact_decay":        round(self.impact_decay, 3),
        }


# ── OpponentAgent ──────────────────────────────────────────────────────────── #

@dataclass
class OpponentAgent:
    """
    An opponent agent with a behavioral type and a genome.

    The genome controls risk parameters (sl_atr_mult, tp_rr, min_score, lot_scale).
    The opponent_type controls the entry logic (signal generation).

    Parameters
    ----------
    agent_id       : unique integer ID
    opponent_type  : behavioral strategy type
    genome         : strategy parameters (entry thresholds, sizing)
    aggression     : how aggressively this opponent sizes up (0.5–2.0)
    """
    agent_id:      int
    opponent_type: OpponentType
    genome:        AgentGenome
    aggression:    float = 1.0   # lot sizing multiplier relative to genome

    def decision(
        self,
        closes:     np.ndarray,
        highs:      np.ndarray,
        lows:       np.ndarray,
        i:          int,
        wave_state: str,
        atr:        float,
        price_adj:  float,      # market impact adjustment to effective price
    ) -> Optional[Tuple[str, float]]:
        """
        Make entry decision at bar i.

        Returns (direction, lot_fraction) or None if no entry.
        lot_fraction is in (0, 1] relative to genome.lot_scale × aggression.
        """
        if atr <= _EPS or i < _LOOKBACK:
            return None

        window = closes[max(0, i - _LOOKBACK): i + 1]
        if len(window) < 4:
            return None

        diffs    = np.diff(window)
        up_frac  = float(np.sum(diffs > 0)) / max(len(diffs), 1)
        down_frac = 1.0 - up_frac

        return self._apply_strategy(
            closes, highs, lows, i, wave_state, atr,
            up_frac, down_frac, window, price_adj,
        )

    def _apply_strategy(
        self,
        closes:     np.ndarray,
        highs:      np.ndarray,
        lows:       np.ndarray,
        i:          int,
        wave_state: str,
        atr:        float,
        up_frac:    float,
        down_frac:  float,
        window:     np.ndarray,
        price_adj:  float,
    ) -> Optional[Tuple[str, float]]:
        """Dispatch to per-type strategy logic."""
        lot = self.genome.lot_scale * self.aggression

        if self.opponent_type == OpponentType.MOMENTUM_FOLLOWER:
            # Enter in the direction of recent momentum
            threshold = 0.60
            if up_frac > threshold:
                return ("BUY", lot)
            if down_frac > threshold:
                return ("SELL", lot)
            return None

        if self.opponent_type == OpponentType.MEAN_REVERTER:
            # Fade moves beyond ±k×ATR from short-term mean
            mid = float(np.mean(window[-8:]))
            curr = closes[i]
            k = 1.5
            if curr > mid + k * atr:
                return ("SELL", lot * 0.7)    # fade the move up
            if curr < mid - k * atr:
                return ("BUY", lot * 0.7)     # fade the move down
            return None

        if self.opponent_type == OpponentType.NOISE_TRADER:
            # Random entry with 15% probability (no directional edge)
            if random.random() < 0.15:
                direction = "BUY" if random.random() > 0.5 else "SELL"
                return (direction, lot * 0.5)
            return None

        if self.opponent_type == OpponentType.MARKET_MAKER:
            # Market maker provides liquidity; simplified as being always present
            # but with very tight sizing and symmetric activity
            if random.random() < 0.40:
                direction = "BUY" if up_frac < 0.5 else "SELL"
                return (direction, lot * 0.3)
            return None

        if self.opponent_type == OpponentType.TREND_FADER:
            # Fade crowded entries: counter-trade when momentum is EXTREME
            if up_frac > 0.75:
                return ("SELL", lot * 0.8)    # selling into extreme up-momentum
            if down_frac > 0.75:
                return ("BUY", lot * 0.8)     # buying into extreme down-momentum
            return None

        return None


# ── MarketImpactModel ────────────────────────────────────────────────────── #

class MarketImpactModel:
    """
    Models how aggregate agent positions affect the effective execution price.

    When agents crowd into the same side:
      - Buy-side crowding → price pushed up (worse for latecomers)
      - Sell-side crowding → price pushed down (worse for latecomers)

    Impact formula
    --------------
      impact_t = impact_{t-1} × decay + impact_coefficient × net_lot_signed
      effective_price = nominal_price + impact_t × atr

    net_lot_signed > 0 → more buys than sells → price pushed up.
    Our effective entry for BUY at crowded bar is worse (higher price).
    Our effective entry for SELL at crowded bar is worse (lower price).

    Parameters
    ----------
    impact_coefficient : price impact per unit of net crowding (default 0.15)
    decay              : impact decay factor per bar (default 0.80)
    """

    def __init__(
        self,
        impact_coefficient: float = _IMPACT_COEFFICIENT,
        decay:              float = _IMPACT_DECAY,
    ) -> None:
        self.impact_coefficient = impact_coefficient
        self.decay = decay
        self._current_impact: float = 0.0

    def reset(self) -> None:
        """Reset impact state between episodes."""
        self._current_impact = 0.0

    def update(self, net_lot_signed: float) -> float:
        """
        Update impact and return effective price adjustment (in ATR units).

        Parameters
        ----------
        net_lot_signed : sum of lot_fractions (positive = buy side, negative = sell)

        Returns
        -------
        price_adj : impact in ATR units (positive = price pushed up)
        """
        self._current_impact = (
            self._current_impact * self.decay
            + self.impact_coefficient * net_lot_signed
        )
        return self._current_impact

    @property
    def current_impact(self) -> float:
        return self._current_impact

    def effective_price(self, nominal_price: float, atr: float, is_buy: bool) -> float:
        """
        Return the effective execution price after market impact.

        BUY in positive-impact environment → higher (worse) entry.
        SELL in positive-impact environment → higher (better) entry.
        """
        adj = self._current_impact * atr
        if is_buy:
            return nominal_price + adj    # crowded buy → worse entry
        return nominal_price - adj        # crowded sell → better entry for us


# ── MarketImpactStats ────────────────────────────────────────────────────── #

@dataclass
class MarketImpactStats:
    """Summary statistics for market impact over all episodes."""
    mean_impact:         float = 0.0   # average |impact| per bar
    max_impact:          float = 0.0   # worst crowding event
    crowded_bars_pct:    float = 0.0   # % of bars where |impact| > 0.5 ATR
    avg_price_slippage:  float = 0.0   # average degradation to our entry

    def to_dict(self) -> Dict[str, float]:
        return {
            "mean_impact":        round(self.mean_impact,        4),
            "max_impact":         round(self.max_impact,         4),
            "crowded_bars_pct":   round(self.crowded_bars_pct,   3),
            "avg_price_slippage": round(self.avg_price_slippage, 4),
        }


# ── NashEquilibrium ───────────────────────────────────────────────────────── #

@dataclass
class NashEquilibrium:
    """
    Approximate Nash equilibrium found by iterative best-response.

    our_strategy   : AgentGenome that is our best response to opponents
    opponent_profile: Dict[opponent_type → aggression] at equilibrium
    is_approximate  : always True (exact Nash requires LP for continuous games)
    iterations_used : how many IBR iterations until convergence
    convergence_gap : final |strategy_profile[t] - strategy_profile[t-1]|
    nash_value      : our ecosystem profit_factor at equilibrium
    """
    our_strategy:      AgentGenome
    opponent_profile:  Dict[str, float]    # opponent_type → aggression at equilibrium
    is_approximate:    bool = True
    iterations_used:   int  = 0
    convergence_gap:   float = 0.0
    nash_value:        float = 0.0         # our PF at equilibrium

    def to_dict(self) -> Dict[str, Any]:
        return {
            "our_strategy":      self.our_strategy.to_dict(),
            "opponent_profile":  {k: round(v, 3) for k, v in self.opponent_profile.items()},
            "is_approximate":    self.is_approximate,
            "iterations_used":   self.iterations_used,
            "convergence_gap":   round(self.convergence_gap, 5),
            "nash_value":        round(self.nash_value,       3),
        }


# ── EcosystemSimulator ────────────────────────────────────────────────────── #

class EcosystemSimulator:
    """
    Multi-agent bar-by-bar market simulator.

    For each bar in an episode:
      1. Our agent (genome) evaluates the signal and optionally enters.
      2. All opponent agents evaluate the signal and optionally enter.
      3. MarketImpactModel adjusts effective prices based on net volume.
      4. Our agent's trade uses the impact-adjusted price.
      5. SL/TP are forward-simulated for all open positions.

    Returns per-agent PnL streams and market impact statistics.
    """

    def __init__(
        self,
        opponents:           List[OpponentAgent],
        episodes:            int,
        bars:                int,
        impact_model:        MarketImpactModel,
        seed:                Optional[int] = None,
    ) -> None:
        self.opponents    = opponents
        self.episodes     = episodes
        self.bars         = bars
        self.impact_model = impact_model
        self._gen         = SyntheticCandleGenerator(seq_len=bars, seed=seed)
        self._rng         = random.Random(seed)

    def simulate(
        self, our_genome: AgentGenome
    ) -> Tuple[List[float], List[float], List[bool], MarketImpactStats]:
        """
        Run multi-agent simulation for all episodes.

        Returns
        -------
        (our_pnls, our_rrs, our_wins, impact_stats)
        """
        our_pnls:     List[float] = []
        our_rrs:      List[float] = []
        our_wins:     List[bool]  = []
        all_impacts:  List[float] = []
        slippage_sum: float       = 0.0
        crowded_bars: int         = 0
        total_bars:   int         = 0

        wave_mix = (
            ["BULL_MAIN"] * 3
            + ["BEAR_MAIN"] * 3
            + ["SIDEWAYS"] * 2
        )

        for ep_i in range(self.episodes):
            ws  = wave_mix[ep_i % len(wave_mix)]
            df  = self._gen.generate(ws, n_candles=self.bars + _LOOKBACK)
            self.impact_model.reset()

            ep_pnls, ep_rrs, ep_wins, ep_impacts, ep_slip, ep_crowd, ep_total = (
                self._simulate_episode(our_genome, df, ws)
            )

            our_pnls.extend(ep_pnls)
            our_rrs.extend(ep_rrs)
            our_wins.extend(ep_wins)
            all_impacts.extend(ep_impacts)
            slippage_sum  += ep_slip
            crowded_bars  += ep_crowd
            total_bars    += ep_total

        # Build MarketImpactStats
        if all_impacts:
            arr = np.array(all_impacts)
            mean_impact    = float(np.mean(np.abs(arr)))
            max_impact     = float(np.max(np.abs(arr))) if len(arr) else 0.0
        else:
            mean_impact = max_impact = 0.0

        crowded_pct    = crowded_bars / max(total_bars, 1)
        avg_slip       = slippage_sum / max(len(our_pnls), 1)

        stats = MarketImpactStats(
            mean_impact         = round(mean_impact,  4),
            max_impact          = round(max_impact,   4),
            crowded_bars_pct    = round(crowded_pct,  3),
            avg_price_slippage  = round(avg_slip,     4),
        )

        return our_pnls, our_rrs, our_wins, stats

    def _simulate_episode(
        self,
        our_genome: AgentGenome,
        df,
        wave_state: str,
    ) -> Tuple[
        List[float], List[float], List[bool],
        List[float], float, int, int,
    ]:
        """
        Bar-by-bar multi-agent episode simulation.

        Returns
        -------
        (our_pnls, our_rrs, our_wins, impact_history,
         total_slippage, crowded_bar_count, total_bars)
        """
        closes = df["close"].values
        highs  = df["high"].values
        lows   = df["low"].values

        our_pnls:        List[float] = []
        our_rrs:         List[float] = []
        our_wins:        List[bool]  = []
        impact_history:  List[float] = []
        total_slippage:  float       = 0.0
        crowded_bars:    int         = 0

        i      = _LOOKBACK
        n      = len(closes)
        skip_until = 0      # bar after which we can enter a new trade

        while i < n - 1:
            atr = self._compute_atr(highs, lows, closes, i)
            if atr <= _EPS:
                i += 1
                continue

            # ── 1. All opponent decisions this bar ─────────────────── #
            net_opponent_lot: float = 0.0
            for opp in self.opponents:
                decision = opp.decision(
                    closes, highs, lows, i, wave_state, atr,
                    price_adj=self.impact_model.current_impact,
                )
                if decision is not None:
                    direction, lot = decision
                    signed_lot = lot if direction == "BUY" else -lot
                    net_opponent_lot += signed_lot

            # ── 2. Our signal ─────────────────────────────────────── #
            our_score, our_direction = self._compute_signal(
                closes, highs, lows, i, wave_state, atr, our_genome
            )

            # ── 3. Market impact from all agent activity ──────────── #
            our_lot_signed: float = 0.0
            if i >= skip_until and our_score >= our_genome.min_score and our_direction:
                our_lot_signed = our_genome.lot_scale if our_direction == "BUY" else -our_genome.lot_scale

            net_lot_signed = net_opponent_lot + our_lot_signed
            impact = self.impact_model.update(net_lot_signed)
            impact_history.append(impact)

            if abs(impact) > 0.5:
                crowded_bars += 1

            # ── 4. Our trade execution with impact-adjusted price ─── #
            if i >= skip_until and our_score >= our_genome.min_score and our_direction:
                nominal_entry = closes[i]
                is_buy        = our_direction == "BUY"
                entry         = self.impact_model.effective_price(nominal_entry, atr, is_buy)
                slippage      = abs(entry - nominal_entry) / (atr + _EPS)
                total_slippage += slippage

                sl_dist = atr * our_genome.sl_atr_mult
                tp_dist = sl_dist * our_genome.tp_rr

                if is_buy:
                    sl = entry - sl_dist
                    tp = entry + tp_dist
                else:
                    sl = entry + sl_dist
                    tp = entry - tp_dist

                outcome_pnl, outcome_rr, hit_tp = self._forward_sim(
                    closes, highs, lows, i + 1, n,
                    entry, sl, tp, sl_dist, tp_dist, our_direction,
                )

                our_pnls.append(outcome_pnl)
                our_rrs.append(outcome_rr)
                our_wins.append(hit_tp)

                skip_until = i + max(3, int(our_genome.sl_atr_mult * 2))

            i += 1

        return (
            our_pnls, our_rrs, our_wins,
            impact_history, total_slippage, crowded_bars, n - _LOOKBACK,
        )

    @staticmethod
    def _compute_atr(
        highs:  np.ndarray,
        lows:   np.ndarray,
        closes: np.ndarray,
        i:      int,
    ) -> float:
        start = max(0, i - _LOOKBACK)
        trs = []
        for j in range(start + 1, i + 1):
            hl  = highs[j]  - lows[j]
            hc  = abs(highs[j]  - closes[j - 1])
            lc  = abs(lows[j]   - closes[j - 1])
            trs.append(max(hl, hc, lc))
        return float(np.mean(trs)) if trs else 0.0

    @staticmethod
    def _compute_signal(
        closes:     np.ndarray,
        highs:      np.ndarray,
        lows:       np.ndarray,
        i:          int,
        wave_state: str,
        atr:        float,
        genome:     AgentGenome,
    ) -> Tuple[float, Optional[str]]:
        """Lightweight signal (mirrors MarketSimulator._compute_signal)."""
        window = closes[max(0, i - _LOOKBACK): i + 1]
        if len(window) < 4:
            return 0.0, None

        diffs    = np.diff(window)
        up_frac  = float(np.sum(diffs > 0)) / max(len(diffs), 1)
        down_frac = 1.0 - up_frac

        body         = abs(closes[i] - (highs[i] + lows[i]) / 2)
        candle_score = min(body / (atr + _EPS), 1.0)

        if wave_state == "BULL_MAIN":
            direction_score = up_frac
            direction = "BUY" if up_frac > 0.50 else None
        elif wave_state == "BEAR_MAIN":
            direction_score = down_frac
            direction = "SELL" if down_frac > 0.50 else None
        else:
            mid  = (max(window) + min(window)) / 2
            curr = closes[i]
            if curr < mid - 0.3 * atr:
                direction, direction_score = "BUY", 0.55
            elif curr > mid + 0.3 * atr:
                direction, direction_score = "SELL", 0.55
            else:
                return 0.0, None

        if direction is None:
            return 0.0, None

        wave_conf = min(abs(direction_score - 0.5) * 2.0, 1.0)
        if wave_conf < genome.wave_conf_floor:
            return 0.0, None

        avg_mw = sum(genome.mode_weights.values()) / max(len(genome.mode_weights), 1)
        score  = wave_conf * candle_score * avg_mw
        return round(score, 4), direction

    @staticmethod
    def _forward_sim(
        closes:    np.ndarray,
        highs:     np.ndarray,
        lows:      np.ndarray,
        start:     int,
        end:       int,
        entry:     float,
        sl:        float,
        tp:        float,
        sl_dist:   float,
        tp_dist:   float,
        direction: str,
    ) -> Tuple[float, float, bool]:
        """Walk forward until SL/TP hit or episode ends."""
        is_long = direction == "BUY"
        for j in range(start, min(end, start + 30)):
            lo = lows[j]
            hi = highs[j]
            if is_long:
                if lo <= sl:
                    return -1.0, -1.0, False
                if hi >= tp:
                    return tp_dist / max(sl_dist, _EPS), tp_dist / max(sl_dist, _EPS), True
            else:
                if hi >= sl:
                    return -1.0, -1.0, False
                if lo <= tp:
                    return tp_dist / max(sl_dist, _EPS), tp_dist / max(sl_dist, _EPS), True

        if sl_dist > _EPS:
            paper = (closes[min(end - 1, start + 29)] - entry) / sl_dist
            if not is_long:
                paper = -paper
            return round(float(paper), 3), abs(float(paper)), paper > 0
        return 0.0, 0.0, False


# ── NashEquilibriumFinder ────────────────────────────────────────────────── #

class NashEquilibriumFinder:
    """
    Iterative Best Response (IBR) algorithm to find approximate Nash equilibrium.

    Round-robin IBR:
    1. Fix opponents at current aggression profile
    2. Find our best response genome (max ecosystem fitness)
    3. For each opponent, compute best response aggression level
       given our current strategy
    4. Repeat until convergence or max_iterations

    Convergence criterion:
      max(|aggression[t] − aggression[t-1]|) < 1e-3
    """

    def find(
        self,
        candidate_genomes: List[AgentGenome],
        opponents:         List[OpponentAgent],
        cfg:               EcosystemConfig,
        seed:              Optional[int] = None,
    ) -> NashEquilibrium:
        """
        Run IBR and return approximate Nash equilibrium.

        Parameters
        ----------
        candidate_genomes : pool of our strategy candidates
        opponents         : list of opponent agents
        cfg               : ecosystem configuration

        Returns
        -------
        NashEquilibrium with our best-response strategy and opponent profile
        """
        impact_model = MarketImpactModel(cfg.impact_coefficient, cfg.impact_decay)

        # ── Initial evaluation: find our best candidate in this ecosystem ── #
        best_genome, best_pf = self._find_best_response(
            candidate_genomes, opponents, cfg, impact_model, seed,
        )

        # ── IBR iterations ──────────────────────────────────────────── #
        opponent_profile = {opp.opponent_type.value: opp.aggression for opp in opponents}
        convergence_gap  = 1.0
        iterations_used  = 0

        for iteration in range(cfg.nash_iterations):
            prev_profile = dict(opponent_profile)

            # Adapt opponent aggressions given our current strategy
            new_profile = self._adapt_opponents(
                best_genome, opponents, cfg, impact_model, seed,
            )

            # Apply adapted profile to opponents
            for opp in opponents:
                t = opp.opponent_type.value
                if t in new_profile:
                    opp.aggression = new_profile[t]
            opponent_profile = new_profile

            # Find our new best response given updated opponents
            best_genome, best_pf = self._find_best_response(
                candidate_genomes, opponents, cfg, impact_model, seed,
            )

            # Check convergence
            changes = [
                abs(opponent_profile.get(k, 1.0) - prev_profile.get(k, 1.0))
                for k in opponent_profile
            ]
            convergence_gap = max(changes) if changes else 0.0
            iterations_used = iteration + 1

            if convergence_gap < 1e-3:
                break

        return NashEquilibrium(
            our_strategy     = best_genome,
            opponent_profile = {k: round(v, 3) for k, v in opponent_profile.items()},
            is_approximate   = True,
            iterations_used  = iterations_used,
            convergence_gap  = round(convergence_gap, 6),
            nash_value       = round(best_pf, 3),
        )

    def _find_best_response(
        self,
        candidate_genomes: List[AgentGenome],
        opponents:         List[OpponentAgent],
        cfg:               EcosystemConfig,
        impact_model:      MarketImpactModel,
        seed:              Optional[int],
    ) -> Tuple[AgentGenome, float]:
        """Evaluate all candidates and return the one with highest ecosystem PF."""
        best_genome: AgentGenome = candidate_genomes[0]
        best_pf:     float       = -1.0

        for genome in candidate_genomes:
            sim    = EcosystemSimulator(opponents, cfg.episodes, cfg.bars_per_episode,
                                        impact_model, seed)
            pnls, rrs, wins, _ = sim.simulate(genome)
            pf     = _compute_pf(pnls)
            if pf > best_pf:
                best_pf     = pf
                best_genome = genome

        return best_genome, best_pf

    def _adapt_opponents(
        self,
        our_genome:   AgentGenome,
        opponents:    List[OpponentAgent],
        cfg:          EcosystemConfig,
        impact_model: MarketImpactModel,
        seed:         Optional[int],
    ) -> Dict[str, float]:
        """
        For each opponent type, find the aggression level that maximises
        their returns against our current strategy.

        Simplified: test aggression ∈ {0.5, 0.75, 1.0, 1.25, 1.5} for each type.
        This is the opponent's best-response to our strategy.
        """
        aggression_options = [0.5, 0.75, 1.0, 1.25, 1.5]
        new_profile: Dict[str, float] = {}

        # Group opponents by type
        type_groups: Dict[str, List[OpponentAgent]] = {}
        for opp in opponents:
            t = opp.opponent_type.value
            if t not in type_groups:
                type_groups[t] = []
            type_groups[t].append(opp)

        for opp_type, group in type_groups.items():
            best_agg  = group[0].aggression
            best_gain = -float("inf")

            for agg in aggression_options:
                # Temporarily set aggression
                for opp in group:
                    opp.aggression = agg

                # Estimate opponent PnL in this configuration
                sim    = EcosystemSimulator(opponents, max(2, cfg.episodes // 2),
                                            cfg.bars_per_episode, impact_model, seed)
                _, _, _, impact_stats = sim.simulate(our_genome)

                # Opponents gain when our slippage is high
                opp_gain = impact_stats.avg_price_slippage + impact_stats.crowded_bars_pct

                # Blend with exploitation_rate
                adaptive_agg = agg * cfg.exploitation_rate + group[0].aggression * (1 - cfg.exploitation_rate)

                if opp_gain > best_gain:
                    best_gain = opp_gain
                    best_agg  = adaptive_agg

            new_profile[opp_type] = round(max(0.5, min(2.0, best_agg)), 3)

        # Restore best aggressions
        for opp in opponents:
            t = opp.opponent_type.value
            if t in new_profile:
                opp.aggression = new_profile[t]

        return new_profile


# ── ExploitabilityScorer ──────────────────────────────────────────────────── #

class ExploitabilityScorer:
    """
    Measures how exploitable our strategy is against each opponent type.

    For each opponent type T, simulates a 1-vs-N (T only) ecosystem
    and measures how our strategy performs.

    exploitability[T] = max(0, 1 − win_rate × pf_norm)

    Interpretation
    --------------
    0.0 → our strategy is completely unexploitable by type T
    1.0 → type T beats us systematically (win_rate → 0 and/or PF → 0)
    """

    def score(
        self,
        our_genome:   AgentGenome,
        all_opponents: List[OpponentAgent],
        cfg:           EcosystemConfig,
        seed:          Optional[int] = None,
    ) -> Dict[str, float]:
        """
        Return exploitability score per opponent type in [0, 1].

        Higher = more exploitable by that type.
        """
        scores:      Dict[str, float] = {}
        impact_model = MarketImpactModel(cfg.impact_coefficient, cfg.impact_decay)

        # Group by type
        type_groups: Dict[str, List[OpponentAgent]] = {}
        for opp in all_opponents:
            t = opp.opponent_type.value
            if t not in type_groups:
                type_groups[t] = []
            type_groups[t].append(opp)

        for opp_type, group in type_groups.items():
            sim  = EcosystemSimulator(
                group, max(2, cfg.episodes // 2),
                cfg.bars_per_episode, impact_model, seed,
            )
            pnls, rrs, wins, _ = sim.simulate(our_genome)

            if not pnls:
                scores[opp_type] = 0.5    # neutral — no data
                continue

            win_rate = sum(1 for w in wins if w) / max(len(wins), 1)
            pf       = _compute_pf(pnls)
            pf_norm  = min(1.0, pf / 2.0)

            exploitability = max(0.0, 1.0 - win_rate * pf_norm)
            scores[opp_type] = round(exploitability, 4)

        return scores


# ── GameTheoryResult ──────────────────────────────────────────────────────── #

@dataclass
class GameTheoryResult:
    """
    Complete output of GameTheoryEngine.run().

    Fields
    ------
    best_response_genome  : AgentGenome optimal in multi-agent ecosystem
    nash_equilibrium      : NashEquilibrium (IBR approximate)
    exploitability        : Dict[opponent_type → score ∈ [0,1]]
    ecosystem_pf          : profit_factor in ecosystem (vs isolation)
    isolation_pf          : profit_factor without market impact (benchmark)
    impact_stats          : MarketImpactStats
    ecosystem_insights    : List[str] human-readable analysis
    ecosystem_config      : EcosystemConfig used
    n_opponents           : actual number of opponents
    duration_secs         : wall-clock time
    applied_to_live       : True after apply_to()
    """
    best_response_genome: AgentGenome
    nash_equilibrium:     NashEquilibrium
    exploitability:       Dict[str, float]
    ecosystem_pf:         float
    isolation_pf:         float
    impact_stats:         MarketImpactStats
    ecosystem_insights:   List[str]
    ecosystem_config:     EcosystemConfig
    n_opponents:          int
    duration_secs:        float
    completed_at:         float = field(default_factory=time.time)
    applied_to_live:      bool  = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "best_response_genome":  self.best_response_genome.to_dict(),
            "nash_equilibrium":      self.nash_equilibrium.to_dict(),
            "exploitability":        {k: round(v, 4) for k, v in self.exploitability.items()},
            "ecosystem_pf":          round(self.ecosystem_pf,  3),
            "isolation_pf":          round(self.isolation_pf,  3),
            "pf_ecosystem_vs_isolation": round(
                self.ecosystem_pf - self.isolation_pf, 3
            ),
            "impact_stats":          self.impact_stats.to_dict(),
            "ecosystem_insights":    self.ecosystem_insights,
            "ecosystem_config":      self.ecosystem_config.to_dict(),
            "n_opponents":           self.n_opponents,
            "duration_secs":         round(self.duration_secs, 2),
            "completed_at":          self.completed_at,
            "applied_to_live":       self.applied_to_live,
        }

    def apply_to(self, decision_engine: Any) -> None:
        """Apply best-response genome to the live DecisionEngine."""
        ctrl = getattr(decision_engine, "controller", None)
        if ctrl is None:
            logger.warning("GameTheoryResult.apply_to: no controller found")
            return

        genome = self.best_response_genome
        for mode in _ALL_MODES:
            mw = genome.mode_weights.get(mode, 1.0)
            for wave_state in _ALL_WAVE_STATES:
                key = f"{mode}/{wave_state}"
                adj = max(-0.50, min(0.50, round(mw - 1.0, 3)))
                ctrl._state.mode_weight_adjs[key] = adj

        ctrl.base_min_score   = round(max(0.10, min(0.50, genome.min_score)), 3)
        ctrl._state.lot_scale = round(max(0.25, min(2.00, genome.lot_scale)), 3)

        self.applied_to_live = True
        logger.info(
            "GameTheoryResult.apply_to: nash_value=%.3f eco_pf=%.3f min_score=%.3f lot=%.3f",
            self.nash_equilibrium.nash_value,
            self.ecosystem_pf,
            genome.min_score,
            genome.lot_scale,
        )


# ── GameTheoryEngine ──────────────────────────────────────────────────────── #

class GameTheoryEngine:
    """
    Multi-Agent Game Theory + Market Ecosystem Engine.

    The strategic ecosystem intelligence layer of the intelligence stack.

    Answers the fundamental ecosystem questions
    -------------------------------------------
    1. What is our best response given known opponent types?
       → find strategy optimal in multi-agent ecosystem, not isolation
    2. What is the Nash equilibrium of this market game?
       → at equilibrium, no agent can unilaterally improve by deviating
    3. Which opponents exploit us most?
       → reveals strategic vulnerabilities; guides defensive strategy selection
    4. How much does market impact degrade our strategy?
       → measures cost of crowding; helps calibrate lot sizes

    Phases
    ------
    Phase 1: Spawn opponent agents (diverse behavioral types)
    Phase 2: Evaluate candidate genomes in ecosystem (with market impact)
    Phase 3: Iterative Best Response → Nash equilibrium
    Phase 4: Exploitability scoring per opponent type
    Phase 5: Build ecosystem insights
    """

    def __init__(
        self,
        config: Optional[EcosystemConfig] = None,
        seed:   Optional[int]              = None,
    ) -> None:
        self.config           = config or EcosystemConfig()
        self.seed             = seed
        self._rng             = random.Random(seed)
        self._last_result: Optional[GameTheoryResult] = None

    @property
    def last_result(self) -> Optional[GameTheoryResult]:
        return self._last_result

    def run(self) -> GameTheoryResult:
        """
        Execute the full game theory pipeline.

        Returns GameTheoryResult with Nash equilibrium, best-response genome,
        exploitability scores, and ecosystem insights.
        """
        t0 = time.time()
        cfg = self.config
        logger.info(
            "GameTheoryEngine: start | n_opponents=%d candidates=%d episodes=%d",
            cfg.n_opponents, cfg.n_candidate_genomes, cfg.episodes,
        )

        # ── Phase 1: Spawn opponents ──────────────────────────────── #
        opponents = self._spawn_opponents(cfg)

        # ── Phase 2: Candidate genome pool ───────────────────────── #
        candidates = self._sample_candidates(cfg)

        # ── Phase 3: Initial ecosystem evaluation (best response) ─── #
        impact_model = MarketImpactModel(cfg.impact_coefficient, cfg.impact_decay)
        eco_pnls, eco_rrs, eco_wins, impact_stats, best_genome = (
            self._evaluate_ecosystem(candidates, opponents, cfg, impact_model)
        )
        ecosystem_pf = _compute_pf(eco_pnls)

        # ── Phase 4: Isolation benchmark (no opponents) ──────────── #
        # Pure single-agent MarketSimulator for comparison
        isolation_sim  = MarketSimulator(
            episodes=cfg.episodes, bars=cfg.bars_per_episode, seed=self.seed,
        )
        isolation_fit  = isolation_sim.evaluate(best_genome)
        isolation_pf   = isolation_fit.profit_factor

        # ── Phase 5: Nash Equilibrium via IBR ────────────────────── #
        nash_finder = NashEquilibriumFinder()
        nash = nash_finder.find(candidates, opponents, cfg, self.seed)

        # ── Phase 6: Exploitability scoring ──────────────────────── #
        exp_scorer  = ExploitabilityScorer()
        exploitability = exp_scorer.score(nash.our_strategy, opponents, cfg, self.seed)

        # ── Phase 7: Insights ─────────────────────────────────────── #
        insights = self._build_insights(
            nash, exploitability, ecosystem_pf, isolation_pf,
            impact_stats, opponents, cfg,
        )

        duration = time.time() - t0
        logger.info(
            "GameTheoryEngine: done in %.2fs | nash_value=%.3f eco_pf=%.3f iso_pf=%.3f",
            duration, nash.nash_value, ecosystem_pf, isolation_pf,
        )

        result = GameTheoryResult(
            best_response_genome = nash.our_strategy,
            nash_equilibrium     = nash,
            exploitability       = exploitability,
            ecosystem_pf         = round(ecosystem_pf, 3),
            isolation_pf         = round(isolation_pf, 3),
            impact_stats         = impact_stats,
            ecosystem_insights   = insights,
            ecosystem_config     = cfg,
            n_opponents          = len(opponents),
            duration_secs        = round(duration, 3),
        )
        self._last_result = result
        return result

    # ── Internal helpers ─────────────────────────────────────────── #

    def _spawn_opponents(self, cfg: EcosystemConfig) -> List[OpponentAgent]:
        """Spawn diverse opponent agents based on config."""
        opponents: List[OpponentAgent] = []
        types  = [OpponentType(t) for t in cfg.opponent_types if t in OpponentType._value2member_map_]
        if not types:
            types = list(OpponentType)

        for idx in range(cfg.n_opponents):
            opp_type   = types[idx % len(types)]
            genome     = AgentGenome.random(self._rng)
            aggression = self._rng.uniform(0.6, 1.4)
            opponents.append(OpponentAgent(
                agent_id      = idx,
                opponent_type = opp_type,
                genome        = genome,
                aggression    = round(aggression, 3),
            ))
        return opponents

    def _sample_candidates(self, cfg: EcosystemConfig) -> List[AgentGenome]:
        """1 default genome + random candidates."""
        candidates = [AgentGenome.default()]
        while len(candidates) < cfg.n_candidate_genomes:
            candidates.append(AgentGenome.random(self._rng))
        return candidates

    def _evaluate_ecosystem(
        self,
        candidates:   List[AgentGenome],
        opponents:    List[OpponentAgent],
        cfg:          EcosystemConfig,
        impact_model: MarketImpactModel,
    ) -> Tuple[List[float], List[float], List[bool], MarketImpactStats, AgentGenome]:
        """Evaluate all candidates in the ecosystem, return best."""
        best_genome  = candidates[0]
        best_pf      = -1.0
        best_pnls:   List[float] = []
        best_rrs:    List[float] = []
        best_wins:   List[bool]  = []
        best_stats   = MarketImpactStats()

        for genome in candidates:
            sim   = EcosystemSimulator(opponents, cfg.episodes, cfg.bars_per_episode,
                                       impact_model, self.seed)
            pnls, rrs, wins, stats = sim.simulate(genome)
            pf    = _compute_pf(pnls)
            if pf > best_pf:
                best_pf    = pf
                best_genome = genome
                best_pnls   = pnls
                best_rrs    = rrs
                best_wins   = wins
                best_stats  = stats

        return best_pnls, best_rrs, best_wins, best_stats, best_genome

    @staticmethod
    def _build_insights(
        nash:           NashEquilibrium,
        exploitability: Dict[str, float],
        ecosystem_pf:   float,
        isolation_pf:   float,
        impact_stats:   MarketImpactStats,
        opponents:      List[OpponentAgent],
        cfg:            EcosystemConfig,
    ) -> List[str]:
        """Build human-readable insights about the strategic ecosystem."""
        insights: List[str] = []

        # Summary
        n_types = len({opp.opponent_type.value for opp in opponents})
        insights.append(
            f"Ecosystem simulation: {len(opponents)} opponents ({n_types} types), "
            f"{cfg.episodes} episodes, Nash IBR converged in {nash.iterations_used} iterations."
        )

        # Nash equilibrium
        insights.append(
            f"NASH EQUILIBRIUM: our strategy value={nash.nash_value:.3f} PF "
            f"(convergence_gap={nash.convergence_gap:.5f})."
        )
        if nash.convergence_gap > 0.05:
            insights.append(
                "  → Equilibrium NOT fully converged — market is highly dynamic. "
                "Strategy adaptation is ongoing."
            )

        # Ecosystem vs isolation comparison
        pf_delta = ecosystem_pf - isolation_pf
        if pf_delta < -0.2:
            insights.append(
                f"MARKET IMPACT: ecosystem_PF={ecosystem_pf:.3f} vs "
                f"isolation_PF={isolation_pf:.3f} (Δ={pf_delta:+.3f}). "
                f"Market impact is SIGNIFICANT — opponents are degrading our entries."
            )
        elif pf_delta > 0.1:
            insights.append(
                f"ECOSYSTEM ADVANTAGE: ecosystem_PF={ecosystem_pf:.3f} vs "
                f"isolation_PF={isolation_pf:.3f} (Δ={pf_delta:+.3f}). "
                f"Our strategy actually BENEFITS from the multi-agent environment."
            )
        else:
            insights.append(
                f"Market impact is MODERATE: ecosystem_PF={ecosystem_pf:.3f} "
                f"vs isolation_PF={isolation_pf:.3f} (Δ={pf_delta:+.3f})."
            )

        # Impact statistics
        insights.append(
            f"CROWDING: {impact_stats.crowded_bars_pct:.1%} of bars had significant "
            f"crowding (|impact| > 0.5 ATR). "
            f"Avg slippage per trade: {impact_stats.avg_price_slippage:.3f}×ATR. "
            f"Max impact event: {impact_stats.max_impact:.3f}×ATR."
        )

        # Exploitability
        sorted_exp = sorted(exploitability.items(), key=lambda kv: kv[1], reverse=True)
        if sorted_exp:
            most_exp = sorted_exp[0]
            least_exp = sorted_exp[-1]
            insights.append(
                f"EXPLOITABILITY: most exploitable by {most_exp[0]} "
                f"(score={most_exp[1]:.3f}); "
                f"least exploitable by {least_exp[0]} "
                f"(score={least_exp[1]:.3f})."
            )
            if most_exp[1] > 0.6:
                insights.append(
                    f"  → WARNING: {most_exp[0]} exploits our strategy significantly. "
                    f"Consider raising min_score or reducing lot_scale to reduce exposure."
                )
            for opp_type, score in sorted_exp[:3]:
                insights.append(
                    f"  {opp_type}: exploitability={score:.3f} "
                    f"({'HIGH' if score > 0.6 else 'MEDIUM' if score > 0.35 else 'LOW'})"
                )

        # Nash opponent profile
        insights.append("NASH OPPONENT PROFILE at equilibrium:")
        for opp_type, agg in nash.opponent_profile.items():
            insights.append(
                f"  {opp_type}: aggression={agg:.3f} "
                f"({'aggressive' if agg > 1.1 else 'passive' if agg < 0.9 else 'neutral'})"
            )

        # Best response genome highlight
        g = nash.our_strategy
        insights.append(
            f"BEST RESPONSE GENOME: min_score={g.min_score:.3f} "
            f"tp_rr={g.tp_rr:.2f} sl_atr={g.sl_atr_mult:.2f} "
            f"wave_floor={g.wave_conf_floor:.2f} lot_scale={g.lot_scale:.3f}."
        )

        # Strategic recommendation
        max_exp = max(exploitability.values()) if exploitability else 0.0
        if max_exp > 0.6 and impact_stats.crowded_bars_pct > 0.20:
            insights.append(
                "STRATEGIC RECOMMENDATION: HIGH exploitability + HIGH crowding detected. "
                "Recommend DEFENSIVE mode: raise min_score, reduce lot, "
                "prefer SIDEWAYS mean-reversion modes over trend-following."
            )
        elif max_exp < 0.3 and pf_delta > -0.1:
            insights.append(
                "STRATEGIC RECOMMENDATION: LOW exploitability + manageable impact. "
                "Strategy is ROBUST in the current ecosystem. "
                "Maintain current parameters; market conditions are favorable."
            )
        else:
            insights.append(
                "STRATEGIC RECOMMENDATION: Monitor crowding levels. "
                "Reduce lot_scale by 10-20% during high-impact periods. "
                "Consider TREND_FADER protection by setting wave_conf_floor higher."
            )

        return insights


# ── Utility helpers ───────────────────────────────────────────────────────── #

def _compute_pf(pnls: List[float]) -> float:
    """Compute profit factor from PnL list."""
    if not pnls:
        return 0.0
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss   = abs(sum(p for p in pnls if p < 0)) + _EPS
    return round(min(gross_profit / gross_loss, 20.0), 4)
