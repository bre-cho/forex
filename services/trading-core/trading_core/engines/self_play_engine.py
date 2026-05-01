"""
Self-Play + Simulation Environment Engine.

Mục tiêu
--------
Biến hệ thống từ **optimization system** → **evolutionary system**:
  hệ tự tạo môi trường  → SyntheticCandleGenerator + MarketSimulator
  tự tạo đối thủ        → Population[AgentGenome]
  tự cạnh tranh         → EvolutionaryEngine.run_generation()
  tự chọn chiến lược    → tournament selection + elitism
  tự tiến hóa           → crossover + mutation over N generations

Architecture
------------
  EvolutionaryEngine
    ├─ Population[AgentGenome]         ← strategy "DNA" (6 gene groups)
    ├─ MarketSimulator                 ← fast bar-by-bar replay engine
    │    ├─ SyntheticCandleGenerator   ← borrowed from synthetic_engine
    │    └─ _simulate_episode()        ← trade logic on simulated bars
    ├─ _evaluate_agent()               ← runs agent on all episodes
    ├─ _select()                       ← tournament selection
    ├─ _crossover()                    ← uniform gene blending
    ├─ _mutate()                       ← Gaussian perturbation
    └─ run_generation()                ← one full EA cycle

AgentGenome (evolvable parameters)
-----------------------------------
  mode_weights    : Dict[mode → multiplier 0.2..2.0]  (7 modes)
  min_score       : entry quality gate  0.10..0.60
  tp_rr           : TP as multiple of SL  1.0..5.0
  sl_atr_mult     : SL = ATR × this  0.8..3.5
  wave_conf_floor : minimum wave confidence to enter  0.20..0.80
  lot_scale       : base lot multiplier  0.25..2.0

Fitness function
----------------
  fitness = profit_factor
            × (1 - max_drawdown_frac)    ← penalise heavy drawdown
            × log1p(total_trades / 20)   ← reward trading activity
  (clipped to [0, 20])

  Prefer agents that:
    • Have high profit_factor
    • Avoid large drawdowns
    • Actually trade (not just hide by raising min_score to infinity)

MarketSimulator (bar-level replay)
------------------------------------
For each bar in an episode:
  1. Compute a lightweight signal score from candle structure + drift alignment.
  2. If score × agent.mode_weights[wave_mode] ≥ agent.min_score:
       - open position at next-bar open
       - SL = ATR × sl_atr_mult, TP = SL × tp_rr
       - walk forward bars until SL/TP hit or episode ends
  3. Collect PnL stream → fitness metrics

Integration with live system
-----------------------------
  result = engine.run()
  result.apply_to(decision_engine)

  apply_to() maps best genome → live AdaptiveController:
    • mode_weight_adjs ← best.mode_weights (converted to adj deltas)
    • base_min_score   ← best.min_score
    • lot_scale        ← best.lot_scale  (capped by safety limits)

API endpoints (wired in main.py)
---------------------------------
  POST /api/evolution/run                → trigger one evolution run
  GET  /api/evolution/status             → EvolutionResult.to_dict()
"""

from __future__ import annotations

import copy
import logging
import math
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .synthetic_engine import SyntheticCandleGenerator, _ALL_MODES, _ALL_WAVE_STATES

logger = logging.getLogger(__name__)

# ── Gene bounds ──────────────────────────────────────────────────────────── #

_MODE_WEIGHT_MIN = 0.20
_MODE_WEIGHT_MAX = 2.00
_MIN_SCORE_MIN   = 0.10
_MIN_SCORE_MAX   = 0.60
_TP_RR_MIN       = 1.0
_TP_RR_MAX       = 5.0
_SL_ATR_MIN      = 0.80
_SL_ATR_MAX      = 3.50
_WAVE_FLOOR_MIN  = 0.20
_WAVE_FLOOR_MAX  = 0.80
_LOT_SCALE_MIN   = 0.25
_LOT_SCALE_MAX   = 2.00

# ── Evolution hyper-parameters ───────────────────────────────────────────── #

_DEFAULT_POP_SIZE     = 20
_DEFAULT_GENERATIONS  = 5
_DEFAULT_EPISODES     = 10     # synthetic episodes per agent evaluation
_DEFAULT_BARS_EPISODE = 80     # bars per episode (80 M5 bars ≈ 7 hours)
_TOURNAMENT_K         = 3      # tournament pool size
_MUTATION_RATE        = 0.25   # probability that each gene is mutated
_MUTATION_SIGMA       = 0.15   # Gaussian std dev for mutation (as fraction of range)
_ELITISM_N            = 2      # top N agents copied unchanged to next gen
_LOOKBACK             = 10     # bars used to compute ATR and drift


# ── AgentGenome ──────────────────────────────────────────────────────────── #

@dataclass
class AgentGenome:
    """
    Strategy "DNA" — a complete set of evolvable trading parameters.

    Can be cloned, mutated, and crossed over.
    """
    mode_weights:      Dict[str, float]  # mode_name → weight multiplier
    min_score:         float             # minimum signal quality to enter
    tp_rr:             float             # TP as multiples of SL distance
    sl_atr_mult:       float             # SL = ATR × this
    wave_conf_floor:   float             # minimum wave confidence to enter
    lot_scale:         float             # position size multiplier
    generation:        int = 0           # generation this genome was born in
    parent_ids:        Tuple[int, int] = field(default=(0, 0))

    @classmethod
    def random(cls, rng: random.Random, gen: int = 0) -> "AgentGenome":
        """Create a genome with uniformly randomised genes."""
        mw = {
            mode: rng.uniform(_MODE_WEIGHT_MIN, _MODE_WEIGHT_MAX)
            for mode in _ALL_MODES
        }
        return cls(
            mode_weights    = mw,
            min_score       = rng.uniform(_MIN_SCORE_MIN, _MIN_SCORE_MAX),
            tp_rr           = rng.uniform(_TP_RR_MIN, _TP_RR_MAX),
            sl_atr_mult     = rng.uniform(_SL_ATR_MIN, _SL_ATR_MAX),
            wave_conf_floor = rng.uniform(_WAVE_FLOOR_MIN, _WAVE_FLOOR_MAX),
            lot_scale       = rng.uniform(_LOT_SCALE_MIN, _LOT_SCALE_MAX),
            generation      = gen,
        )

    @classmethod
    def default(cls) -> "AgentGenome":
        """Baseline genome mirroring the system's default AutoPilot params."""
        mw = {mode: 1.0 for mode in _ALL_MODES}
        # Slightly favour proven trend modes
        mw["BREAKOUT"]       = 1.0
        mw["RETRACEMENT"]    = 1.1
        mw["TREND_PULLBACK"] = 1.1
        mw["RETEST_SAME"]    = 0.95
        mw["RETEST_OPPOSITE"]= 0.70
        return cls(
            mode_weights    = mw,
            min_score       = 0.25,
            tp_rr           = 2.0,
            sl_atr_mult     = 1.5,
            wave_conf_floor = 0.35,
            lot_scale       = 1.0,
        )

    def clone(self) -> "AgentGenome":
        return copy.deepcopy(self)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode_weights":      {k: round(v, 4) for k, v in self.mode_weights.items()},
            "min_score":         round(self.min_score, 4),
            "tp_rr":             round(self.tp_rr, 3),
            "sl_atr_mult":       round(self.sl_atr_mult, 3),
            "wave_conf_floor":   round(self.wave_conf_floor, 3),
            "lot_scale":         round(self.lot_scale, 3),
            "generation":        self.generation,
        }


# ── AgentFitness ─────────────────────────────────────────────────────────── #

@dataclass
class AgentFitness:
    """Performance metrics + composite fitness for one AgentGenome evaluation."""
    profit_factor:   float = 0.0
    win_rate:        float = 0.0
    avg_rr:          float = 0.0
    max_drawdown:    float = 0.0   # fraction 0–1 of peak equity
    total_trades:    int   = 0
    total_pnl:       float = 0.0
    fitness:         float = 0.0   # composite fitness score

    def to_dict(self) -> Dict[str, Any]:
        return {
            "profit_factor": round(self.profit_factor, 3),
            "win_rate":      round(self.win_rate, 3),
            "avg_rr":        round(self.avg_rr, 3),
            "max_drawdown":  round(self.max_drawdown, 3),
            "total_trades":  self.total_trades,
            "total_pnl":     round(self.total_pnl, 4),
            "fitness":       round(self.fitness, 4),
        }


# ── EvolutionResult ──────────────────────────────────────────────────────── #

@dataclass
class EvolutionResult:
    """Complete result of one EvolutionaryEngine.run() call."""
    best_genome:           AgentGenome
    best_fitness:          AgentFitness
    population_fitnesses:  List[AgentFitness]   # all agents, last generation
    generation_bests:      List[float]          # best fitness per generation
    generations_run:       int
    population_size:       int
    episodes_per_agent:    int
    duration_secs:         float
    completed_at:          float = field(default_factory=time.time)
    applied_to_live:       bool  = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "best_genome":        self.best_genome.to_dict(),
            "best_fitness":       self.best_fitness.to_dict(),
            "generation_bests":   [round(f, 4) for f in self.generation_bests],
            "generations_run":    self.generations_run,
            "population_size":    self.population_size,
            "episodes_per_agent": self.episodes_per_agent,
            "duration_secs":      round(self.duration_secs, 2),
            "completed_at":       self.completed_at,
            "applied_to_live":    self.applied_to_live,
            "avg_population_fitness": round(
                sum(f.fitness for f in self.population_fitnesses)
                / max(len(self.population_fitnesses), 1),
                4,
            ),
        }

    def apply_to(self, decision_engine: Any) -> None:
        """
        Apply the best evolved genome's parameters to the live DecisionEngine.

        Mapping
        -------
        best.mode_weights  → controller.state.mode_weight_adjs
                             (converted: adj = weight − 1.0, clamped ±0.5)
        best.min_score     → controller.base_min_score
        best.lot_scale     → controller.state.lot_scale  (clamped 0.25..1.5)
        """
        genome = self.best_genome
        ctrl   = getattr(decision_engine, "controller", None)
        if ctrl is None:
            logger.warning("EvolutionResult.apply_to: no controller found")
            return

        # Mode weight adjustments (convert multiplier → delta from 1.0)
        for mode in _ALL_MODES:
            mw = genome.mode_weights.get(mode, 1.0)
            for wave_state in _ALL_WAVE_STATES:
                key = f"{mode}/{wave_state}"
                adj = max(-0.50, min(0.50, round(mw - 1.0, 3)))
                ctrl._state.mode_weight_adjs[key] = adj

        # Min score
        ctrl.base_min_score = round(
            max(0.10, min(0.50, genome.min_score)), 3
        )

        # Lot scale (conservative cap at 1.5 for safety)
        ctrl._state.lot_scale = round(
            max(0.25, min(1.50, genome.lot_scale)), 3
        )

        self.applied_to_live = True
        logger.info(
            "EvolutionResult.apply_to: applied best genome gen=%d "
            "fitness=%.4f min_score=%.3f lot_scale=%.3f",
            genome.generation,
            self.best_fitness.fitness,
            genome.min_score,
            genome.lot_scale,
        )


# ── MarketSimulator ───────────────────────────────────────────────────────── #

class MarketSimulator:
    """
    Lightweight bar-by-bar market replay engine.

    For each bar in an episode it:
      1. Computes a signal quality score using candle structure + drift.
      2. Applies the agent's mode_weights and wave_conf_floor filter.
      3. If a trade is triggered, forward-simulates SL/TP outcome.
      4. Collects PnL stream → AgentFitness.

    Deliberately simple to stay fast (no external I/O, pure numpy).
    The scoring logic intentionally mirrors the decision logic in
    DecisionEngine._predict_regime() and AutoPilot._score_candidate().
    """

    _RISK_PER_TRADE = 0.01   # 1% of equity per trade (fixed fractional)

    def __init__(
        self,
        episodes:   int = _DEFAULT_EPISODES,
        bars:       int = _DEFAULT_BARS_EPISODE,
        seed:       Optional[int] = None,
    ) -> None:
        self.episodes = episodes
        self.bars     = bars
        self._gen     = SyntheticCandleGenerator(
            seq_len=bars, seed=seed
        )
        self._rng     = random.Random(seed)
        self._np_rng  = np.random.default_rng(seed)

    def evaluate(self, genome: AgentGenome) -> AgentFitness:
        """
        Run the agent genome through all episodes and return aggregate fitness.
        """
        all_pnls:   List[float] = []
        all_rrs:    List[float] = []
        all_wins:   List[bool]  = []
        equity_curve: List[float] = [1.0]

        # Mix of wave states so the agent is tested on all regimes
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

            # Update equity curve per episode
            eq = equity_curve[-1]
            for pnl in ep_pnls:
                eq = eq * (1.0 + pnl * self._RISK_PER_TRADE)
            equity_curve.append(max(eq, 1e-6))

        return self._compute_fitness(all_pnls, all_rrs, all_wins, equity_curve)

    # ── Internal ──────────────────────────────────────────────────────── #

    def _simulate_episode(
        self,
        genome:     AgentGenome,
        df,          # pd.DataFrame
        wave_state: str,
    ) -> Tuple[List[float], List[float], List[bool]]:
        """
        Bar-by-bar simulation of one episode.

        Returns (pnl_list, rr_list, win_list).
        pnl_list: PnL in units of initial_risk (+1 = full TP hit, -1 = full SL hit).
        """
        closes = df["close"].values
        highs  = df["high"].values
        lows   = df["low"].values

        pnls: List[float] = []
        rrs:  List[float] = []
        wins: List[bool]  = []

        i = _LOOKBACK     # start after lookback warm-up
        n = len(closes)

        while i < n - 1:
            # Compute ATR over last _LOOKBACK bars
            atr = self._compute_atr(highs, lows, closes, i)
            if atr <= 0:
                i += 1
                continue

            # Signal score: alignment of last bar with wave direction
            score, direction = self._compute_signal(
                closes, highs, lows, i, wave_state, atr, genome
            )

            if score < genome.min_score or direction is None:
                i += 1
                continue

            # Open position at next bar open
            entry = closes[i]    # use close as proxy for next open
            sl_dist = atr * genome.sl_atr_mult
            tp_dist = sl_dist * genome.tp_rr

            if direction == "BUY":
                sl = entry - sl_dist
                tp = entry + tp_dist
            else:
                sl = entry + sl_dist
                tp = entry - tp_dist

            # Forward simulate until SL/TP or end of episode
            outcome_pnl, outcome_rr, hit_tp = self._forward_sim(
                closes, highs, lows, i + 1, n,
                entry, sl, tp, sl_dist, tp_dist, direction
            )

            pnls.append(outcome_pnl)
            rrs.append(outcome_rr)
            wins.append(hit_tp)

            # Skip bars used by this trade before looking for next entry
            # (simplified: skip 3 bars to avoid overtrading in simulation)
            i += max(3, int(genome.sl_atr_mult * 2))

        return pnls, rrs, wins

    @staticmethod
    def _compute_atr(
        highs: np.ndarray,
        lows:  np.ndarray,
        closes: np.ndarray,
        i: int,
    ) -> float:
        """Simple ATR as average of True Range over last _LOOKBACK bars."""
        start = max(0, i - _LOOKBACK)
        trs = []
        for j in range(start + 1, i + 1):
            hl = highs[j] - lows[j]
            hc = abs(highs[j] - closes[j - 1])
            lc = abs(lows[j] - closes[j - 1])
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
        """
        Lightweight signal scoring (mirrors DecisionEngine + AutoPilot logic).

        Returns (score, direction) or (0.0, None) if no signal.
        """
        window = closes[max(0, i - _LOOKBACK): i + 1]
        if len(window) < 4:
            return 0.0, None

        # Drift: fraction of last _LOOKBACK bars moving in each direction
        diffs = np.diff(window)
        up_frac   = float(np.sum(diffs > 0)) / max(len(diffs), 1)
        down_frac = 1.0 - up_frac

        # Body ratio of current bar
        body   = abs(closes[i] - (highs[i] + lows[i]) / 2)
        candle_score = min(body / (atr + 1e-9), 1.0)

        # Wave alignment bonus
        if wave_state == "BULL_MAIN":
            direction_score = up_frac
            direction = "BUY" if up_frac > 0.50 else None
        elif wave_state == "BEAR_MAIN":
            direction_score = down_frac
            direction = "SELL" if down_frac > 0.50 else None
        else:
            # SIDEWAYS: prefer mean-reversion signals
            mid = (max(window) + min(window)) / 2
            curr = closes[i]
            if curr < mid - 0.3 * atr:
                direction       = "BUY"
                direction_score = 0.55
            elif curr > mid + 0.3 * atr:
                direction       = "SELL"
                direction_score = 0.55
            else:
                return 0.0, None

        if direction is None:
            return 0.0, None

        # Wave confidence proxy: variance-normalised drift strength
        wave_conf = min(abs(direction_score - 0.5) * 2.0, 1.0)
        if wave_conf < genome.wave_conf_floor:
            return 0.0, None

        # Mode weight: use the maximum weight across all modes as a boost
        max_mw = max(genome.mode_weights.values()) if genome.mode_weights else 1.0
        # Scale to match AutoPilot's scoring range
        avg_mw = sum(genome.mode_weights.values()) / max(len(genome.mode_weights), 1)

        score = wave_conf * candle_score * avg_mw
        return round(score, 4), direction

    @staticmethod
    def _forward_sim(
        closes:   np.ndarray,
        highs:    np.ndarray,
        lows:     np.ndarray,
        start:    int,
        end:      int,
        entry:    float,
        sl:       float,
        tp:       float,
        sl_dist:  float,
        tp_dist:  float,
        direction: str,
    ) -> Tuple[float, float, bool]:
        """
        Walk forward from bar `start` until SL/TP hit or episode ends.

        Returns (pnl_in_risk_units, rr_achieved, hit_tp).
        """
        is_long = direction == "BUY"
        for j in range(start, min(end, start + 30)):
            lo = lows[j]
            hi = highs[j]
            cl = closes[j]
            if is_long:
                if lo <= sl:
                    return -1.0, -1.0, False
                if hi >= tp:
                    return tp_dist / sl_dist, tp_dist / sl_dist, True
            else:
                if hi >= sl:
                    return -1.0, -1.0, False
                if lo <= tp:
                    return tp_dist / sl_dist, tp_dist / sl_dist, True
        # Timeout: exit at last close
        if sl_dist > 1e-9:
            paper = (closes[min(end - 1, start + 29)] - entry) / sl_dist
            if not is_long:
                paper = -paper
            return round(float(paper), 3), abs(float(paper)), paper > 0
        return 0.0, 0.0, False

    @staticmethod
    def _compute_fitness(
        pnls: List[float],
        rrs:  List[float],
        wins: List[bool],
        equity: List[float],
    ) -> AgentFitness:
        """Aggregate trade-level metrics into a single AgentFitness."""
        if not pnls:
            return AgentFitness(fitness=0.0)

        total_trades = len(pnls)
        win_count    = sum(1 for w in wins if w)
        win_rate     = win_count / total_trades

        gross_profit = sum(p for p in pnls if p > 0)
        gross_loss   = abs(sum(p for p in pnls if p < 0)) + 1e-9
        # Cap profit_factor to prevent numerical instability when gross_loss is
        # near-zero (all trades profitable — uncommon but possible in idealized
        # synthetic markets).
        pf           = min(gross_profit / gross_loss, 20.0)
        avg_rr       = float(np.mean(rrs)) if rrs else 0.0
        total_pnl    = float(sum(pnls))

        # Max drawdown from equity curve
        peak = equity[0]
        max_dd = 0.0
        for eq in equity:
            peak  = max(peak, eq)
            dd    = (peak - eq) / (peak + 1e-9)
            max_dd = max(max_dd, dd)

        # Fitness: profit_factor × drawdown penalty × activity bonus
        # activity uses sqrt to reduce reward for over-trading
        activity = math.sqrt(total_trades / 20.0)
        fitness  = pf * (1.0 - max_dd) * activity
        fitness  = round(min(max(fitness, 0.0), 100.0), 4)

        return AgentFitness(
            profit_factor = round(pf,       3),
            win_rate      = round(win_rate,  3),
            avg_rr        = round(avg_rr,    3),
            max_drawdown  = round(max_dd,    3),
            total_trades  = total_trades,
            total_pnl     = round(total_pnl, 4),
            fitness       = fitness,
        )


# ── EvolutionaryEngine ────────────────────────────────────────────────────── #

class EvolutionaryEngine:
    """
    Evolutionary Strategy Optimizer.

    Manages a population of AgentGenome objects, evolves them over
    multiple generations, and returns the fittest strategy.

    Parameters
    ----------
    pop_size        : population size (agents)
    generations     : number of evolution cycles
    episodes        : synthetic market episodes per evaluation
    bars_per_episode: candle bars per episode
    seed            : random seed for reproducibility
    """

    def __init__(
        self,
        pop_size:         int   = _DEFAULT_POP_SIZE,
        generations:      int   = _DEFAULT_GENERATIONS,
        episodes:         int   = _DEFAULT_EPISODES,
        bars_per_episode: int   = _DEFAULT_BARS_EPISODE,
        seed:             Optional[int] = None,
    ) -> None:
        from trading_core.engines._advanced_guard import require_advanced_engines
        require_advanced_engines("EvolutionaryEngine")
        self.pop_size         = pop_size
        self.generations      = generations
        self.episodes         = episodes
        self.bars_per_episode = bars_per_episode
        self.seed             = seed

        self._rng      = random.Random(seed)
        self._last_result: Optional[EvolutionResult] = None

    @property
    def last_result(self) -> Optional[EvolutionResult]:
        return self._last_result

    def run(self) -> EvolutionResult:
        """
        Run the full evolutionary loop.

        Returns EvolutionResult with the best genome found.
        """
        t0 = time.time()
        logger.info(
            "EvolutionaryEngine: starting | pop=%d gen=%d episodes=%d bars=%d",
            self.pop_size, self.generations, self.episodes, self.bars_per_episode,
        )

        sim = MarketSimulator(
            episodes=self.episodes,
            bars=self.bars_per_episode,
            seed=self.seed,
        )

        # Initialise population (include one default genome for stability)
        population: List[AgentGenome] = [AgentGenome.default()]
        while len(population) < self.pop_size:
            population.append(AgentGenome.random(self._rng, gen=0))

        gen_bests: List[float] = []
        best_genome:  AgentGenome  = population[0].clone()
        best_fitness: AgentFitness = AgentFitness()

        for gen in range(self.generations):
            # ── Evaluate ────────────────────────────────────────────── #
            fitnesses: List[AgentFitness] = [
                sim.evaluate(agent) for agent in population
            ]

            # Track best
            for ag, ft in zip(population, fitnesses):
                if ft.fitness > best_fitness.fitness:
                    best_fitness = ft
                    best_genome  = ag.clone()
                    best_genome.generation = gen

            gen_best = max(ft.fitness for ft in fitnesses)
            gen_bests.append(gen_best)
            logger.info(
                "EvolutionaryEngine gen %d/%d: best_fitness=%.4f avg=%.4f",
                gen + 1, self.generations,
                gen_best,
                sum(f.fitness for f in fitnesses) / max(len(fitnesses), 1),
            )

            if gen == self.generations - 1:
                break   # don't build new population after last gen

            # ── Build next generation ────────────────────────────────── #
            ranked = sorted(
                zip(population, fitnesses),
                key=lambda x: x[1].fitness,
                reverse=True,
            )

            next_pop: List[AgentGenome] = []

            # Elitism: carry over top _ELITISM_N unchanged
            for elite, _ in ranked[:_ELITISM_N]:
                cloned = elite.clone()
                cloned.generation = gen + 1
                next_pop.append(cloned)

            # Fill rest via tournament selection + crossover + mutation
            while len(next_pop) < self.pop_size:
                parent_a = self._tournament_select(population, fitnesses)
                parent_b = self._tournament_select(population, fitnesses)
                child    = self._crossover(parent_a, parent_b, gen + 1)
                child    = self._mutate(child)
                next_pop.append(child)

            population = next_pop

        duration = time.time() - t0
        logger.info(
            "EvolutionaryEngine: done in %.2fs | "
            "best fitness=%.4f (gen=%d) pf=%.3f wr=%.2f%% dd=%.2f%%",
            duration,
            best_fitness.fitness,
            best_genome.generation,
            best_fitness.profit_factor,
            best_fitness.win_rate * 100,
            best_fitness.max_drawdown * 100,
        )

        result = EvolutionResult(
            best_genome          = best_genome,
            best_fitness         = best_fitness,
            population_fitnesses = fitnesses,
            generation_bests     = gen_bests,
            generations_run      = self.generations,
            population_size      = self.pop_size,
            episodes_per_agent   = self.episodes,
            duration_secs        = round(duration, 3),
        )
        self._last_result = result
        return result

    # ── Evolutionary operators ────────────────────────────────────────── #

    def _tournament_select(
        self,
        population: List[AgentGenome],
        fitnesses:  List[AgentFitness],
    ) -> AgentGenome:
        """Select one genome via tournament of size _TOURNAMENT_K."""
        indices   = self._rng.sample(range(len(population)), min(_TOURNAMENT_K, len(population)))
        best_idx  = max(indices, key=lambda i: fitnesses[i].fitness)
        return population[best_idx]

    def _crossover(
        self,
        a: AgentGenome,
        b: AgentGenome,
        gen: int,
    ) -> AgentGenome:
        """
        Uniform blend crossover.

        Each scalar gene is drawn from U[min(a,b), max(a,b)].
        Each mode_weight is independently blended.
        """
        def _blend(va: float, vb: float) -> float:
            lo, hi = min(va, vb), max(va, vb)
            return self._rng.uniform(lo, hi) if lo < hi else va

        mw = {
            mode: _blend(
                a.mode_weights.get(mode, 1.0),
                b.mode_weights.get(mode, 1.0),
            )
            for mode in _ALL_MODES
        }
        return AgentGenome(
            mode_weights    = mw,
            min_score       = _blend(a.min_score,       b.min_score),
            tp_rr           = _blend(a.tp_rr,           b.tp_rr),
            sl_atr_mult     = _blend(a.sl_atr_mult,     b.sl_atr_mult),
            wave_conf_floor = _blend(a.wave_conf_floor, b.wave_conf_floor),
            lot_scale       = _blend(a.lot_scale,       b.lot_scale),
            generation      = gen,
            parent_ids      = (id(a), id(b)),
        )

    def _mutate(self, genome: AgentGenome) -> AgentGenome:
        """
        Gaussian perturbation with probability _MUTATION_RATE per gene.

        Mutation magnitude = _MUTATION_SIGMA × gene_range.
        Mutated values are clamped to gene bounds.
        """
        if self._rng.random() >= _MUTATION_RATE:
            return genome  # no mutation this individual

        g = genome.clone()

        def _perturb(val: float, lo: float, hi: float) -> float:
            if self._rng.random() < _MUTATION_RATE:
                delta = self._rng.gauss(0, _MUTATION_SIGMA * (hi - lo))
                val   = max(lo, min(hi, val + delta))
            return val

        for mode in _ALL_MODES:
            g.mode_weights[mode] = _perturb(
                g.mode_weights.get(mode, 1.0),
                _MODE_WEIGHT_MIN, _MODE_WEIGHT_MAX,
            )
        g.min_score       = _perturb(g.min_score,       _MIN_SCORE_MIN,  _MIN_SCORE_MAX)
        g.tp_rr           = _perturb(g.tp_rr,           _TP_RR_MIN,      _TP_RR_MAX)
        g.sl_atr_mult     = _perturb(g.sl_atr_mult,     _SL_ATR_MIN,     _SL_ATR_MAX)
        g.wave_conf_floor = _perturb(g.wave_conf_floor, _WAVE_FLOOR_MIN, _WAVE_FLOOR_MAX)
        g.lot_scale       = _perturb(g.lot_scale,       _LOT_SCALE_MIN,  _LOT_SCALE_MAX)
        return g
