"""
Decision Theory + Utility Optimization Engine.

Mục tiêu
--------
Nâng cấp từ **causal strategic intelligence** → **rational strategic agent**:

  Hệ không chỉ tối ưu win/loss (fitness scalar) hay causal_score,
  mà tối ưu theo **utility đa chiều dài hạn**:

    • growth vs trust          → E[log(wealth)] vs max_drawdown
    • speed vs stability       → trade frequency vs equity smoothness
    • short-term vs long-term  → recency-weighted dominance split

  Bước chuyển: causal intelligence → rational strategic agent.

Architecture
------------
  UtilityOptimizationEngine
    ├─ Phase 1: Sampling + Extended Evaluation
    │    └─ ExtendedSimulator.evaluate_rich(genome)
    │         → RichFitness: pnl_series, equity_curve, sharpe, calmar,
    │           kelly_fraction, first/second_half_pf
    │
    ├─ Phase 2: Utility Vectorisation
    │    └─ UtilityFunction.compute(rich_fitness, config)
    │         → UtilityVector: growth_u, trust_u, stability_u,
    │            speed_u, dominance_u, composite
    │
    ├─ Phase 3: Pareto Frontier Analysis
    │    └─ ParetoFrontier.compute(utility_vectors)
    │         → Pareto-efficient genome indices (no dominated strategies)
    │
    ├─ Phase 4: Rational Agent Selection
    │    └─ RationalAgent.select(genomes, utility_vectors, config)
    │         → best genome by expected utility
    │         → Kelly-optimal lot_scale (risk-aversion adjusted)
    │
    └─ UtilityOptimizationResult
         ├─ optimal_genome:     AgentGenome (utility-maximising strategy)
         ├─ kelly_lot_scale:    float (Kelly-optimal position size)
         ├─ utility_vectors:    Dict[genome_id → UtilityVector]
         ├─ pareto_indices:     List[int] (Pareto-efficient genome indices)
         ├─ utility_config:     UtilityConfig used
         ├─ utility_insights:   List[str] (trade-off analysis)
         └─ apply_to(decision_engine)

Utility Dimensions
------------------
  growth_u    : E[log(1 + r)] = Kelly-optimal geometric growth rate
                Maximises long-run wealth accumulation.
                Risk-aversion shifts weight away from growth.
  trust_u     : (1 − max_drawdown) × capped_profit_factor
                High trust = strategy that doesn't blow up.
  stability_u : Sharpe-like equity smoothness measure.
                High stability = consistent, predictable returns.
  speed_u     : Trade frequency relative to expected volume.
                More trades = more data but also more noise + slippage.
  dominance_u : Long-term vs short-term regime dominance.
                Controlled by time_preference (0=myopic, 1=far-sighted).
                Far-sighted = recent performance matters more.

UtilityConfig (all configurable at runtime)
-------------------------------------------
  growth_weight:      default 0.35 — weight on geometric growth
  trust_weight:       default 0.30 — weight on drawdown safety
  stability_weight:   default 0.20 — weight on equity smoothness
  speed_weight:       default 0.10 — weight on trade activity
  dominance_weight:   default 0.05 — weight on long-term dominance
  risk_aversion:      default 0.4  — 0=risk-neutral, 1=fully risk-averse
  time_preference:    default 0.6  — 0=myopic (first half), 1=far-sighted (recent)
  kelly_safety_factor: default 0.25 — fraction of full Kelly for lot sizing

Trade-offs expressed
--------------------
  growth vs trust:    growth_weight ↑ / trust_weight ↓ → more aggressive
  speed vs stability: speed_weight ↑ / stability_weight ↓ → trade more often
  short vs long-term: time_preference → 0=value recent wins, 1=value all history

KellyOptimizer
--------------
  kelly_fraction = max(0, win_rate − (1 − win_rate) / max(avg_rr, 0.5))
  kelly_safety   = kelly_safety_factor × (1 − 0.5 × risk_aversion)
  lot_scale      = clamp(0.25 + kelly_fraction × kelly_safety × 7.0, 0.25, 2.0)

ParetoFrontier
--------------
  Genome G is Pareto-dominated if ∃ genome H such that:
    H.utility_i ≥ G.utility_i for ALL i, AND H.utility_j > G.utility_j for SOME j
  Pareto-efficient set: genomes that are NOT dominated.
  The rational agent selects the Pareto genome with highest composite utility.

API endpoints (wired in main.py)
---------------------------------
  POST /api/utility/run        → trigger optimization (slow, ≥15s)
  GET  /api/utility/status     → UtilityOptimizationResult.to_dict()
  GET  /api/utility/pareto     → Pareto frontier data for visualisation
  POST /api/utility/apply      → apply optimal genome + Kelly lot sizing
  POST /api/utility/configure  → update UtilityConfig weights (no re-run)
"""

from __future__ import annotations

import logging
import math
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .self_play_engine import (
    AgentFitness,
    AgentGenome,
    MarketSimulator,
    _ALL_MODES,
    _LOOKBACK,
)
from .synthetic_engine import _ALL_WAVE_STATES

logger = logging.getLogger(__name__)

# ── Default hyper-parameters ─────────────────────────────────────────────── #

_DEFAULT_N_GENOMES        = 25   # genomes to evaluate per utility run
_DEFAULT_EPISODES         = 8
_DEFAULT_BARS_PER_EPISODE = 70
_SPEED_TARGET_TRADES      = 20   # expected trades per episode (normalisation ref)
_EPS                      = 1e-9


# ── UtilityConfig ─────────────────────────────────────────────────────────── #

@dataclass
class UtilityConfig:
    """
    Configurable weights and preferences for the utility function.

    Weights should sum to 1.0 for the composite to be interpretable.
    The engine normalises them automatically.

    Parameters
    ----------
    growth_weight       : weight on E[log(wealth)] growth
    trust_weight        : weight on drawdown safety
    stability_weight    : weight on equity curve smoothness
    speed_weight        : weight on trade frequency
    dominance_weight    : weight on long-term regime dominance
    risk_aversion       : 0=risk-neutral, 1=fully risk-averse
    time_preference     : 0=myopic (first half matters), 1=far-sighted (second half)
    kelly_safety_factor : fraction of full Kelly criterion to use for lot sizing
    """
    growth_weight:       float = 0.35
    trust_weight:        float = 0.30
    stability_weight:    float = 0.20
    speed_weight:        float = 0.10
    dominance_weight:    float = 0.05
    risk_aversion:       float = 0.40
    time_preference:     float = 0.60
    kelly_safety_factor: float = 0.25

    def normalised_weights(self) -> Dict[str, float]:
        """Return weight dict normalised to sum 1.0."""
        total = (
            self.growth_weight + self.trust_weight + self.stability_weight
            + self.speed_weight + self.dominance_weight + _EPS
        )
        return {
            "growth":    self.growth_weight    / total,
            "trust":     self.trust_weight     / total,
            "stability": self.stability_weight / total,
            "speed":     self.speed_weight     / total,
            "dominance": self.dominance_weight / total,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "growth_weight":       round(self.growth_weight,       3),
            "trust_weight":        round(self.trust_weight,        3),
            "stability_weight":    round(self.stability_weight,    3),
            "speed_weight":        round(self.speed_weight,        3),
            "dominance_weight":    round(self.dominance_weight,    3),
            "risk_aversion":       round(self.risk_aversion,       3),
            "time_preference":     round(self.time_preference,     3),
            "kelly_safety_factor": round(self.kelly_safety_factor, 3),
        }

    @classmethod
    def growth_focused(cls) -> "UtilityConfig":
        """Growth-maximising config (high risk tolerance)."""
        return cls(growth_weight=0.50, trust_weight=0.20, stability_weight=0.15,
                   speed_weight=0.10, dominance_weight=0.05, risk_aversion=0.1)

    @classmethod
    def conservative(cls) -> "UtilityConfig":
        """Capital-preservation config (low risk tolerance)."""
        return cls(growth_weight=0.20, trust_weight=0.45, stability_weight=0.25,
                   speed_weight=0.05, dominance_weight=0.05, risk_aversion=0.8)

    @classmethod
    def long_term(cls) -> "UtilityConfig":
        """Long-term dominance config (favour far-sighted strategies)."""
        return cls(growth_weight=0.30, trust_weight=0.25, stability_weight=0.20,
                   speed_weight=0.10, dominance_weight=0.15,
                   risk_aversion=0.4, time_preference=0.8)


# ── UtilityVector ─────────────────────────────────────────────────────────── #

@dataclass
class UtilityVector:
    """
    Multi-dimensional utility decomposition for one genome.

    All components are in [0, 1]. Higher = better.

    growth_u    : E[log(wealth)] utility — geometric growth rate
    trust_u     : safety = 1 − max_drawdown × (1 − capped_pf_norm)
    stability_u : equity smoothness (Sharpe-normalised)
    speed_u     : trade activity utility (tanh-normalised)
    dominance_u : long-term regime dominance (time_preference split)
    composite   : weighted combination using UtilityConfig weights
    """
    growth_u:    float = 0.0
    trust_u:     float = 0.0
    stability_u: float = 0.0
    speed_u:     float = 0.0
    dominance_u: float = 0.0
    composite:   float = 0.0

    def to_list(self) -> List[float]:
        return [self.growth_u, self.trust_u, self.stability_u,
                self.speed_u, self.dominance_u]

    def to_dict(self) -> Dict[str, float]:
        return {
            "growth_u":    round(self.growth_u,    4),
            "trust_u":     round(self.trust_u,     4),
            "stability_u": round(self.stability_u, 4),
            "speed_u":     round(self.speed_u,     4),
            "dominance_u": round(self.dominance_u, 4),
            "composite":   round(self.composite,   4),
        }

    def dominates(self, other: "UtilityVector") -> bool:
        """Return True if self Pareto-dominates other (≥ all, > some)."""
        self_v   = self.to_list()
        other_v  = other.to_list()
        all_geq  = all(s >= o - _EPS for s, o in zip(self_v, other_v))
        some_gt  = any(s >  o + _EPS for s, o in zip(self_v, other_v))
        return all_geq and some_gt


# ── RichFitness ───────────────────────────────────────────────────────────── #

@dataclass
class RichFitness:
    """
    Extended fitness with metrics needed for utility computation.

    Wraps AgentFitness and adds:
    - pnl_series: raw PnL per trade (in risk-units)
    - equity_curve: equity level after each episode
    - log_returns: log(1 + pnl × risk_per_trade) per trade
    - sharpe: Sharpe-like ratio (mean / std of pnl_series)
    - calmar: total_pnl / max_drawdown (capital efficiency)
    - first_half_pf: profit_factor of first 50% of trades (consistency early)
    - second_half_pf: profit_factor of second 50% of trades (consistency late)
    - kelly_fraction: Kelly-optimal bet fraction ∈ [0, 1]
    """
    base:            AgentFitness
    pnl_series:      List[float]
    equity_curve:    List[float]
    log_returns:     List[float]
    sharpe:          float = 0.0
    calmar:          float = 0.0
    first_half_pf:   float = 0.0
    second_half_pf:  float = 0.0
    kelly_fraction:  float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            **self.base.to_dict(),
            "sharpe":          round(self.sharpe,         4),
            "calmar":          round(self.calmar,         4),
            "first_half_pf":   round(self.first_half_pf,  3),
            "second_half_pf":  round(self.second_half_pf, 3),
            "kelly_fraction":  round(self.kelly_fraction,  4),
        }


# ── ExtendedSimulator ─────────────────────────────────────────────────────── #

class ExtendedSimulator(MarketSimulator):
    """
    MarketSimulator subclass that returns RichFitness via evaluate_rich().

    Runs the same bar-by-bar simulation as the base class but retains
    the raw pnl_series and equity_curve so utility metrics can be computed.
    """

    def evaluate_rich(self, genome: AgentGenome) -> RichFitness:
        """
        Evaluate a genome and return RichFitness with extended metrics.

        Mirrors MarketSimulator.evaluate() but preserves all intermediate
        data required for utility decomposition.
        """
        all_pnls:     List[float] = []
        all_rrs:      List[float] = []
        all_wins:     List[bool]  = []
        equity_curve: List[float] = [1.0]

        wave_mix = (
            ["BULL_MAIN"] * 4
            + ["BEAR_MAIN"] * 4
            + ["SIDEWAYS"] * 2
        )

        for ep_i in range(self.episodes):
            ws = wave_mix[ep_i % len(wave_mix)]
            df = self._gen.generate(ws, n_candles=self.bars + _LOOKBACK)
            ep_pnls, ep_rrs, ep_wins = self._simulate_episode(genome, df, ws)
            all_pnls.extend(ep_pnls)
            all_rrs.extend(ep_rrs)
            all_wins.extend(ep_wins)

            eq = equity_curve[-1]
            for pnl in ep_pnls:
                eq = eq * (1.0 + pnl * self._RISK_PER_TRADE)
            equity_curve.append(max(eq, 1e-6))

        base_fitness = self._compute_fitness(all_pnls, all_rrs, all_wins, equity_curve)
        return self._enrich(base_fitness, all_pnls, equity_curve)

    @staticmethod
    def _enrich(
        base:         AgentFitness,
        pnl_series:   List[float],
        equity_curve: List[float],
    ) -> RichFitness:
        """Compute extended metrics from raw simulation outputs."""
        n = len(pnl_series)
        risk_per_trade = MarketSimulator._RISK_PER_TRADE

        # Log returns: log(1 + pnl × risk_per_trade) per trade
        log_returns: List[float] = []
        for p in pnl_series:
            lr = math.log(max(1.0 + p * risk_per_trade, _EPS))
            log_returns.append(lr)

        # Sharpe: mean(pnl) / std(pnl) — proxy without annualisation
        if n >= 2:
            arr  = np.array(pnl_series)
            sharpe = float(np.mean(arr) / (np.std(arr) + _EPS))
        else:
            sharpe = 0.0

        # Calmar: total_pnl / max_drawdown
        calmar = (
            base.total_pnl / (base.max_drawdown + _EPS)
            if base.max_drawdown > _EPS else base.total_pnl
        )

        # First / second half profit factors
        mid = max(1, n // 2)
        first_half  = pnl_series[:mid]
        second_half = pnl_series[mid:]

        first_half_pf  = _half_pf(first_half)
        second_half_pf = _half_pf(second_half)

        # Kelly fraction: max(0, p − q / avg_rr)
        kelly_fraction = max(0.0, min(1.0, (
            base.win_rate
            - (1.0 - base.win_rate) / max(base.avg_rr, 0.5)
        )))

        return RichFitness(
            base           = base,
            pnl_series     = pnl_series,
            equity_curve   = equity_curve,
            log_returns    = log_returns,
            sharpe         = round(sharpe,         4),
            calmar         = round(calmar,         4),
            first_half_pf  = round(first_half_pf,  4),
            second_half_pf = round(second_half_pf, 4),
            kelly_fraction = round(kelly_fraction,  4),
        )


def _half_pf(pnls: List[float]) -> float:
    """Compute profit factor for a subset of PnL series."""
    if not pnls:
        return 0.0
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss   = abs(sum(p for p in pnls if p < 0)) + _EPS
    return round(min(gross_profit / gross_loss, 20.0), 4)


# ── UtilityFunction ───────────────────────────────────────────────────────── #

class UtilityFunction:
    """
    Transforms RichFitness → UtilityVector using the UtilityConfig.

    Each dimension uses a monotone normalisation (tanh / sigmoid / linear)
    so values land in [0, 1] and can be weighted and summed.

    Risk-aversion modifies the growth dimension:
      risk_aversion = 0 → pure arithmetic mean return (risk-neutral)
      risk_aversion = 1 → pure log-return / Kelly criterion (fully risk-averse)
    """

    def compute(
        self,
        rf:  RichFitness,
        cfg: UtilityConfig,
    ) -> UtilityVector:
        """Transform RichFitness → UtilityVector with per-dimension utilities."""

        # ── Growth utility ─────────────────────────────────────────────── #
        # Blend arithmetic mean return (risk-neutral) and E[log(wealth)] (Kelly)
        pnl_arr = np.array(rf.pnl_series) if rf.pnl_series else np.zeros(1)
        arith_mean = float(np.mean(pnl_arr))
        log_mean   = float(np.mean(rf.log_returns)) if rf.log_returns else 0.0
        # Blend: (1−r)×arith + r×log, then transform to [0,1] via tanh
        blended  = (1.0 - cfg.risk_aversion) * arith_mean + cfg.risk_aversion * log_mean * 100
        growth_u = float(max(0.0, math.tanh(blended)))

        # ── Trust utility ──────────────────────────────────────────────── #
        # (1 − max_drawdown) × normalised profit_factor
        pf_norm  = min(1.0, rf.base.profit_factor / 3.0)
        trust_u  = float((1.0 - rf.base.max_drawdown) * pf_norm)
        trust_u  = max(0.0, min(1.0, trust_u))

        # ── Stability utility ──────────────────────────────────────────── #
        # Sharpe-based: tanh(sharpe) maps R→[0,1) for positive Sharpe
        stability_u = float(max(0.0, math.tanh(max(0.0, rf.sharpe))))

        # ── Speed utility ──────────────────────────────────────────────── #
        # tanh(total_trades / target) → [0, 1)
        target    = max(1, _SPEED_TARGET_TRADES * rf.base.total_trades / max(rf.base.total_trades, _SPEED_TARGET_TRADES))
        speed_u   = float(math.tanh(rf.base.total_trades / _SPEED_TARGET_TRADES))
        speed_u   = max(0.0, min(1.0, speed_u))

        # ── Dominance utility ──────────────────────────────────────────── #
        # time_preference: 0=first_half_matters, 1=second_half_matters
        # (recent performance = second half because trades ordered in time)
        first_norm  = min(1.0, rf.first_half_pf  / 3.0)
        second_norm = min(1.0, rf.second_half_pf / 3.0)
        dominance_u = (
            (1.0 - cfg.time_preference) * first_norm
            + cfg.time_preference       * second_norm
        )
        dominance_u = max(0.0, min(1.0, dominance_u))

        # ── Composite: weighted sum using normalised config weights ─────── #
        w = cfg.normalised_weights()
        composite = (
            w["growth"]    * growth_u
            + w["trust"]     * trust_u
            + w["stability"] * stability_u
            + w["speed"]     * speed_u
            + w["dominance"] * dominance_u
        )
        composite = round(max(0.0, min(1.0, composite)), 4)

        return UtilityVector(
            growth_u    = round(growth_u,    4),
            trust_u     = round(trust_u,     4),
            stability_u = round(stability_u, 4),
            speed_u     = round(speed_u,     4),
            dominance_u = round(dominance_u, 4),
            composite   = composite,
        )


# ── ParetoFrontier ───────────────────────────────────────────────────────── #

class ParetoFrontier:
    """
    Pareto-efficiency analysis for a set of UtilityVectors.

    A genome is Pareto-efficient if no other genome is weakly better
    on ALL dimensions AND strictly better on at LEAST ONE dimension.

    The Pareto frontier represents strategies that make explicit trade-offs —
    improving one dimension requires sacrificing another.
    """

    @staticmethod
    def compute(utility_vectors: List[UtilityVector]) -> List[int]:
        """
        Return indices of Pareto-efficient vectors.

        Parameters
        ----------
        utility_vectors : list of UtilityVector, one per genome

        Returns
        -------
        List of integer indices of Pareto-efficient genomes
        """
        n = len(utility_vectors)
        if n == 0:
            return []

        mat = np.array([v.to_list() for v in utility_vectors], dtype=float)  # (n, 5)

        is_efficient = np.ones(n, dtype=bool)
        for i in range(n):
            if not is_efficient[i]:
                continue
            # Check if any other efficient point dominates i
            others = mat[is_efficient]
            dominated = np.all(others >= mat[i] - _EPS, axis=1) & \
                        np.any(others >  mat[i] + _EPS, axis=1)
            # Don't let i dominate itself
            self_idx = np.sum(is_efficient[:i]) if i > 0 else 0
            if self_idx < len(dominated):
                dominated[self_idx] = False
            if np.any(dominated):
                is_efficient[i] = False

        return [int(i) for i in range(n) if is_efficient[i]]

    @staticmethod
    def pareto_data(
        genomes:         List[AgentGenome],
        utility_vectors: List[UtilityVector],
        pareto_indices:  List[int],
    ) -> List[Dict[str, Any]]:
        """
        Build Pareto frontier data suitable for API / front-end visualisation.

        Returns list of dicts with genome summary + utility vector + is_pareto flag.
        """
        pareto_set = set(pareto_indices)
        return [
            {
                "genome_idx":  i,
                "is_pareto":   i in pareto_set,
                "utility":     utility_vectors[i].to_dict(),
                "genome":      {
                    "min_score":       round(g.min_score,       3),
                    "tp_rr":           round(g.tp_rr,           2),
                    "sl_atr_mult":     round(g.sl_atr_mult,     2),
                    "wave_conf_floor": round(g.wave_conf_floor, 2),
                    "lot_scale":       round(g.lot_scale,       3),
                },
            }
            for i, g in enumerate(genomes)
        ]


# ── KellyOptimizer ────────────────────────────────────────────────────────── #

class KellyOptimizer:
    """
    Kelly criterion-based lot size optimiser.

    Computes the fraction of capital to risk per trade that maximises
    the expected logarithmic growth of equity (geometric mean maximisation).

    Kelly fraction
    --------------
    For a binary bet with win_rate p, avg_win_rr b (win ÷ loss in risk units):
      f* = p − q / b   where q = 1 − p

    This f* is in [0, 1] (clamped) representing the fraction of edge.
    We map it to lot_scale using a linear transformation:
      lot_scale = 0.25 + f* × safety × 7.0

    where safety = kelly_safety_factor × (1 − 0.5 × risk_aversion)

    Risk-aversion effect
    --------------------
    risk_aversion = 0 → full safety factor (risk-neutral)
    risk_aversion = 1 → half safety factor (conservative bet)
    """

    @staticmethod
    def kelly_fraction(rf: RichFitness) -> float:
        """Return the raw Kelly fraction from a RichFitness evaluation."""
        return rf.kelly_fraction  # pre-computed in RichFitness

    @staticmethod
    def optimal_lot_scale(
        rf:  RichFitness,
        cfg: UtilityConfig,
    ) -> float:
        """
        Map Kelly fraction to a lot_scale in [0.25, 2.0].

        Applies risk_aversion to scale down aggressiveness.
        """
        kf     = rf.kelly_fraction
        safety = cfg.kelly_safety_factor * (1.0 - 0.5 * cfg.risk_aversion)
        raw    = 0.25 + kf * safety * 7.0
        return round(max(0.25, min(2.0, raw)), 3)


# ── RationalAgent ─────────────────────────────────────────────────────────── #

class RationalAgent:
    """
    Rational strategic agent: selects actions by maximising expected utility.

    Unlike EvolutionaryEngine (which maximises a fixed fitness function),
    RationalAgent:
      1. Evaluates each genome's multi-dimensional utility vector
      2. Restricts candidates to the Pareto frontier (no dominated strategies)
      3. Selects the Pareto-efficient genome with highest composite utility
      4. Computes Kelly-optimal lot_scale with risk-aversion adjustment
      5. apply_to(): writes the optimal policy to the live DecisionEngine

    Rational = acts consistently with a coherent utility function;
               never chooses a Pareto-dominated strategy.
    """

    def select(
        self,
        genomes:          List[AgentGenome],
        utility_vectors:  List[UtilityVector],
        rich_fitnesses:   List[RichFitness],
        pareto_indices:   List[int],
        cfg:              UtilityConfig,
    ) -> Tuple[AgentGenome, UtilityVector, RichFitness, float]:
        """
        Select the optimal genome from the Pareto-efficient candidates.

        Among Pareto-efficient genomes, selects the one with highest
        composite utility score.

        Returns (genome, utility_vector, rich_fitness, kelly_lot_scale).
        """
        # Use Pareto frontier if non-empty; fall back to all genomes
        candidates = pareto_indices if pareto_indices else list(range(len(genomes)))

        best_idx = max(candidates, key=lambda i: utility_vectors[i].composite)

        genome     = genomes[best_idx]
        uvec       = utility_vectors[best_idx]
        rf         = rich_fitnesses[best_idx]
        lot_scale  = KellyOptimizer.optimal_lot_scale(rf, cfg)

        return genome, uvec, rf, lot_scale

    def apply_to(
        self,
        decision_engine: Any,
        genome:          AgentGenome,
        lot_scale:       float,
    ) -> None:
        """
        Apply the rational agent's optimal policy to the live DecisionEngine.

        Mapping
        -------
        genome.mode_weights  → controller.state.mode_weight_adjs (clamp ±0.5)
        genome.min_score     → controller.base_min_score
        lot_scale (Kelly)    → controller.state.lot_scale (replaces simple evolution)
        """
        ctrl = getattr(decision_engine, "controller", None)
        if ctrl is None:
            logger.warning("RationalAgent.apply_to: no controller found")
            return

        for mode in _ALL_MODES:
            mw = genome.mode_weights.get(mode, 1.0)
            for wave_state in _ALL_WAVE_STATES:
                key = f"{mode}/{wave_state}"
                adj = max(-0.50, min(0.50, round(mw - 1.0, 3)))
                ctrl._state.mode_weight_adjs[key] = adj

        ctrl.base_min_score   = round(max(0.10, min(0.50, genome.min_score)), 3)
        ctrl._state.lot_scale = round(max(0.25, min(2.00, lot_scale)), 3)

        logger.info(
            "RationalAgent.apply_to: min_score=%.3f kelly_lot_scale=%.3f",
            genome.min_score,
            lot_scale,
        )


# ── UtilityOptimizationResult ─────────────────────────────────────────────── #

@dataclass
class UtilityOptimizationResult:
    """
    Complete output of UtilityOptimizationEngine.run().

    Fields
    ------
    optimal_genome     : genome that maximises expected utility on Pareto frontier
    optimal_utility    : its UtilityVector
    optimal_rf         : its RichFitness (includes Sharpe, Calmar, Kelly, etc.)
    kelly_lot_scale    : Kelly-optimal position size (risk-aversion adjusted)
    all_genomes        : all evaluated genomes
    utility_vectors    : UtilityVector per genome
    rich_fitnesses     : RichFitness per genome
    pareto_indices     : indices of Pareto-efficient genomes
    utility_config     : UtilityConfig used for this run
    utility_insights   : human-readable trade-off analysis
    n_genomes          : number of genomes evaluated
    duration_secs      : wall-clock time for the run
    applied_to_live    : True after apply_to() is called
    """
    optimal_genome:  AgentGenome
    optimal_utility: UtilityVector
    optimal_rf:      RichFitness
    kelly_lot_scale: float
    all_genomes:     List[AgentGenome]
    utility_vectors: List[UtilityVector]
    rich_fitnesses:  List[RichFitness]
    pareto_indices:  List[int]
    utility_config:  UtilityConfig
    utility_insights: List[str]
    n_genomes:       int
    duration_secs:   float
    completed_at:    float = field(default_factory=time.time)
    applied_to_live: bool  = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "optimal_genome":     self.optimal_genome.to_dict(),
            "optimal_utility":    self.optimal_utility.to_dict(),
            "optimal_rf":         self.optimal_rf.to_dict(),
            "kelly_lot_scale":    round(self.kelly_lot_scale, 3),
            "pareto_indices":     self.pareto_indices,
            "pareto_count":       len(self.pareto_indices),
            "n_genomes":          self.n_genomes,
            "utility_config":     self.utility_config.to_dict(),
            "utility_insights":   self.utility_insights,
            "duration_secs":      round(self.duration_secs, 2),
            "completed_at":       self.completed_at,
            "applied_to_live":    self.applied_to_live,
            # Summary of all genome utilities
            "all_utilities": [
                {
                    "genome_idx": i,
                    "composite":  round(v.composite, 4),
                    "is_pareto":  i in set(self.pareto_indices),
                }
                for i, v in enumerate(self.utility_vectors)
            ],
        }

    def pareto_data(self) -> List[Dict[str, Any]]:
        """Return Pareto frontier data for visualisation."""
        return ParetoFrontier.pareto_data(
            self.all_genomes,
            self.utility_vectors,
            self.pareto_indices,
        )

    def apply_to(self, decision_engine: Any) -> None:
        """Apply the rational agent's optimal policy to the live system."""
        agent = RationalAgent()
        agent.apply_to(decision_engine, self.optimal_genome, self.kelly_lot_scale)
        self.applied_to_live = True
        logger.info(
            "UtilityOptimizationResult.apply_to: composite_utility=%.4f "
            "kelly_lot_scale=%.3f",
            self.optimal_utility.composite,
            self.kelly_lot_scale,
        )


# ── UtilityOptimizationEngine ─────────────────────────────────────────────── #

class UtilityOptimizationEngine:
    """
    Decision Theory + Utility Optimization Engine.

    The rational strategic agent layer of the intelligence stack.

    Answers four trade-off questions
    ---------------------------------
    1. growth vs trust:
       Which strategy grows wealth fastest WITHOUT blowing up?
       → Kelly criterion + drawdown safety trade-off

    2. speed vs stability:
       Trade frequently (more data, more compound growth) vs
       trade less often (smoother equity, lower noise)?
       → speed_u vs stability_u

    3. short-term vs long-term dominance:
       Optimise for strategies that win NOW vs strategies that win
       CONSISTENTLY over time?
       → time_preference in dominance_u (first vs second half PF)

    4. risk-neutral vs risk-averse:
       Maximise expected return vs maximise expected log-return?
       → risk_aversion blending arithmetic and geometric growth

    Phases
    ------
    Phase 1: Sample N genomes (1 default + N-1 random)
    Phase 2: Evaluate each with ExtendedSimulator.evaluate_rich()
    Phase 3: Compute UtilityVector for each genome via UtilityFunction
    Phase 4: Pareto frontier analysis (remove dominated strategies)
    Phase 5: RationalAgent selection + Kelly lot sizing
    Phase 6: Build human-readable utility insights

    Parameters
    ----------
    n_genomes        : number of genomes to sample (default 25)
    episodes         : market episodes per evaluation (default 8)
    bars_per_episode : bars per episode (default 70)
    utility_config   : UtilityConfig (default if None)
    seed             : random seed
    """

    def __init__(
        self,
        n_genomes:        int                     = _DEFAULT_N_GENOMES,
        episodes:         int                     = _DEFAULT_EPISODES,
        bars_per_episode: int                     = _DEFAULT_BARS_PER_EPISODE,
        utility_config:   Optional[UtilityConfig] = None,
        seed:             Optional[int]           = None,
    ) -> None:
        self.n_genomes        = n_genomes
        self.episodes         = episodes
        self.bars_per_episode = bars_per_episode
        self.utility_config   = utility_config or UtilityConfig()
        self.seed             = seed
        self._rng             = random.Random(seed)
        self._last_result: Optional[UtilityOptimizationResult] = None

    @property
    def last_result(self) -> Optional[UtilityOptimizationResult]:
        return self._last_result

    def reconfigure(self, config: UtilityConfig) -> None:
        """Update utility config without re-running the simulation."""
        self.utility_config = config
        # If we have a last result, recompute utilities + re-select optimal
        if self._last_result is not None:
            self._recompute_with_config(config)

    def run(self) -> UtilityOptimizationResult:
        """
        Execute the full utility optimisation pipeline.

        Returns UtilityOptimizationResult with optimal genome,
        Kelly lot scale, Pareto frontier, and utility insights.
        """
        t0 = time.time()
        logger.info(
            "UtilityOptimizationEngine: start | n_genomes=%d episodes=%d bars=%d",
            self.n_genomes, self.episodes, self.bars_per_episode,
        )

        # ── Phase 1: Sample genomes ──────────────────────────────────── #
        genomes = self._sample_genomes()

        # ── Phase 2: Extended evaluation ─────────────────────────────── #
        sim = ExtendedSimulator(
            episodes=self.episodes,
            bars=self.bars_per_episode,
            seed=self.seed,
        )
        rich_fitnesses: List[RichFitness] = []
        for genome in genomes:
            rf = sim.evaluate_rich(genome)
            rich_fitnesses.append(rf)

        # ── Phase 3: Utility vectorisation ───────────────────────────── #
        uf = UtilityFunction()
        utility_vectors = [
            uf.compute(rf, self.utility_config) for rf in rich_fitnesses
        ]

        # ── Phase 4: Pareto frontier ──────────────────────────────────── #
        pareto_indices = ParetoFrontier.compute(utility_vectors)

        # ── Phase 5: Rational agent selection ─────────────────────────── #
        agent = RationalAgent()
        optimal_genome, optimal_utility, optimal_rf, kelly_lot = agent.select(
            genomes         = genomes,
            utility_vectors = utility_vectors,
            rich_fitnesses  = rich_fitnesses,
            pareto_indices  = pareto_indices,
            cfg             = self.utility_config,
        )

        # ── Phase 6: Insights ─────────────────────────────────────────── #
        insights = self._build_insights(
            genomes, utility_vectors, rich_fitnesses,
            pareto_indices, optimal_utility, optimal_rf, kelly_lot,
        )

        duration = time.time() - t0
        logger.info(
            "UtilityOptimizationEngine: done in %.2fs | "
            "optimal_composite=%.4f pareto=%d/%d kelly_lot=%.3f",
            duration,
            optimal_utility.composite,
            len(pareto_indices),
            len(genomes),
            kelly_lot,
        )

        result = UtilityOptimizationResult(
            optimal_genome   = optimal_genome,
            optimal_utility  = optimal_utility,
            optimal_rf       = optimal_rf,
            kelly_lot_scale  = kelly_lot,
            all_genomes      = genomes,
            utility_vectors  = utility_vectors,
            rich_fitnesses   = rich_fitnesses,
            pareto_indices   = pareto_indices,
            utility_config   = self.utility_config,
            utility_insights = insights,
            n_genomes        = len(genomes),
            duration_secs    = round(duration, 3),
        )
        self._last_result = result
        return result

    # ── Internal helpers ───────────────────────────────────────────────── #

    def _sample_genomes(self) -> List[AgentGenome]:
        """Sample diverse genomes: 1 default + N-1 random."""
        pop = [AgentGenome.default()]
        while len(pop) < self.n_genomes:
            pop.append(AgentGenome.random(self._rng))
        return pop

    def _recompute_with_config(self, config: UtilityConfig) -> None:
        """
        Recompute utility vectors and optimal selection using a new config,
        without re-running the simulation.  Called by reconfigure().
        """
        r = self._last_result
        if r is None:
            return
        uf = UtilityFunction()
        new_vectors = [uf.compute(rf, config) for rf in r.rich_fitnesses]
        new_pareto  = ParetoFrontier.compute(new_vectors)
        agent       = RationalAgent()
        opt_g, opt_u, opt_rf, kelly = agent.select(
            genomes         = r.all_genomes,
            utility_vectors = new_vectors,
            rich_fitnesses  = r.rich_fitnesses,
            pareto_indices  = new_pareto,
            cfg             = config,
        )
        insights = self._build_insights(
            r.all_genomes, new_vectors, r.rich_fitnesses,
            new_pareto, opt_u, opt_rf, kelly,
        )
        r.utility_config   = config
        r.utility_vectors  = new_vectors
        r.pareto_indices   = new_pareto
        r.optimal_genome   = opt_g
        r.optimal_utility  = opt_u
        r.optimal_rf       = opt_rf
        r.kelly_lot_scale  = kelly
        r.utility_insights = insights
        r.applied_to_live  = False

    @staticmethod
    def _build_insights(
        genomes:         List[AgentGenome],
        utility_vectors: List[UtilityVector],
        rich_fitnesses:  List[RichFitness],
        pareto_indices:  List[int],
        optimal_utility: UtilityVector,
        optimal_rf:      RichFitness,
        kelly_lot:       float,
    ) -> List[str]:
        """
        Generate human-readable insights explaining the trade-off analysis.

        Covers all four trade-off axes:
        1. growth vs trust
        2. speed vs stability
        3. short-term vs long-term dominance
        4. Kelly-optimal position sizing
        """
        insights: List[str] = []
        n = len(utility_vectors)
        pareto_set = set(pareto_indices)

        # Summary
        insights.append(
            f"Utility optimisation: evaluated {n} genomes, "
            f"Pareto-efficient: {len(pareto_indices)}/{n} "
            f"(non-dominated strategies)."
        )

        # Optimal genome utility breakdown
        u = optimal_utility
        insights.append(
            f"Optimal genome utility: composite={u.composite:.3f} "
            f"[growth={u.growth_u:.3f} trust={u.trust_u:.3f} "
            f"stability={u.stability_u:.3f} speed={u.speed_u:.3f} "
            f"dominance={u.dominance_u:.3f}]"
        )

        # Trade-off 1: growth vs trust
        growth_max  = max(v.growth_u  for v in utility_vectors)
        trust_max   = max(v.trust_u   for v in utility_vectors)
        growth_idx  = max(range(n), key=lambda i: utility_vectors[i].growth_u)
        trust_idx   = max(range(n), key=lambda i: utility_vectors[i].trust_u)

        if growth_idx != trust_idx:
            insights.append(
                f"TRADE-OFF growth vs trust: "
                f"max_growth genome has trust={utility_vectors[growth_idx].trust_u:.3f} "
                f"(drew_down={rich_fitnesses[growth_idx].base.max_drawdown:.2%}); "
                f"max_trust genome has growth={utility_vectors[trust_idx].growth_u:.3f} "
                f"(drew_down={rich_fitnesses[trust_idx].base.max_drawdown:.2%})."
            )
        else:
            insights.append(
                f"Growth and trust agree: both dimensions maximised by the same genome "
                f"(growth={growth_max:.3f}, trust={trust_max:.3f})."
            )

        # Trade-off 2: speed vs stability
        speed_idx   = max(range(n), key=lambda i: utility_vectors[i].speed_u)
        stable_idx  = max(range(n), key=lambda i: utility_vectors[i].stability_u)
        if speed_idx != stable_idx:
            insights.append(
                f"TRADE-OFF speed vs stability: "
                f"fastest genome trades={rich_fitnesses[speed_idx].base.total_trades} "
                f"but stability={utility_vectors[speed_idx].stability_u:.3f}; "
                f"smoothest genome trades={rich_fitnesses[stable_idx].base.total_trades} "
                f"but speed={utility_vectors[stable_idx].speed_u:.3f}."
            )

        # Trade-off 3: short-term vs long-term dominance
        rf_opt = optimal_rf
        if abs(rf_opt.first_half_pf - rf_opt.second_half_pf) > 0.20:
            direction = (
                "improves over time ↑"
                if rf_opt.second_half_pf > rf_opt.first_half_pf
                else "degrades over time ↓"
            )
            insights.append(
                f"SHORT vs LONG-TERM: optimal strategy {direction} — "
                f"first_half_PF={rf_opt.first_half_pf:.3f} → "
                f"second_half_PF={rf_opt.second_half_pf:.3f}."
            )
        else:
            insights.append(
                f"Strategy is STABLE across time: "
                f"first_half_PF={rf_opt.first_half_pf:.3f} ≈ "
                f"second_half_PF={rf_opt.second_half_pf:.3f}."
            )

        # Kelly lot sizing
        kf = rf_opt.kelly_fraction
        insights.append(
            f"KELLY CRITERION: kelly_fraction={kf:.3f} "
            f"→ Kelly-optimal lot_scale={kelly_lot:.3f} "
            f"(win_rate={rf_opt.base.win_rate:.1%} avg_rr={rf_opt.base.avg_rr:.2f})."
        )
        if kelly_lot < 0.5:
            insights.append(
                "  → Kelly recommends CONSERVATIVE sizing — limited statistical edge. "
                "Consider raising min_score or reducing risk-aversion to explore more."
            )
        elif kelly_lot > 1.2:
            insights.append(
                "  → Kelly recommends AGGRESSIVE sizing — strong statistical edge detected. "
                "Monitor drawdown carefully; consider reducing kelly_safety_factor."
            )

        # Pareto frontier interpretation
        if len(pareto_indices) > 1:
            insights.append(
                f"PARETO FRONTIER has {len(pareto_indices)} non-dominated strategies. "
                "Each represents a different growth-trust-stability trade-off. "
                "The 'optimal' choice depends on your risk preferences."
            )
            # Show top 3 Pareto genomes
            top_pareto = sorted(pareto_indices,
                                key=lambda i: utility_vectors[i].composite,
                                reverse=True)[:3]
            for rank, pi in enumerate(top_pareto):
                v = utility_vectors[pi]
                rf = rich_fitnesses[pi]
                insights.append(
                    f"  Pareto #{rank+1}: composite={v.composite:.3f} "
                    f"[growth={v.growth_u:.2f} trust={v.trust_u:.2f} "
                    f"stab={v.stability_u:.2f}] "
                    f"PF={rf.base.profit_factor:.2f} DD={rf.base.max_drawdown:.1%}"
                )

        # Rational agent recommendation
        insights.append(
            f"RATIONAL AGENT recommendation: "
            f"min_score={optimal_rf.base.win_rate:.2f} proxy, "
            f"profit_factor={optimal_rf.base.profit_factor:.3f}, "
            f"max_drawdown={optimal_rf.base.max_drawdown:.1%}, "
            f"Sharpe={optimal_rf.sharpe:.3f}, "
            f"Calmar={optimal_rf.calmar:.3f}."
        )

        return insights
