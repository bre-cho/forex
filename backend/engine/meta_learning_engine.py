"""
Meta-Learning + Strategy Genome Engine.

Mục tiêu
--------
Nâng cấp từ **evolutionary system** → **strategy genetics system**:

  hệ không chỉ chọn winner và tiến hóa winner,
  mà còn học ra:
    • vì sao winner thắng           → WinnerAnalyzer
    • gene chiến lược nào lặp lại   → GenePool
    • gene nào nên giữ / nên loại   → GeneImportance
    • cách tái tổ hợp gene tốt nhất → StrategyGenetics (informed crossover)
    → sinh ra strategy đời mới thông minh hơn

Architecture
------------
  MetaLearningEngine
    ├─ EvolutionaryEngine (phase 1: standard evolution)
    │    └─ EvolutionResult → population fitnesses + genomes
    ├─ WinnerAnalyzer       (phase 2: learn from winners)
    │    ├─ _extract_winner_genomes()   ← top-K per run
    │    ├─ _compute_gene_importance()  ← Pearson correlation: gene ↔ fitness
    │    └─ GenePool.absorb()           ← accumulate across runs
    ├─ StrategyGenetics     (phase 3: genetics-informed breeding)
    │    ├─ breed()          ← importance-weighted crossover
    │    └─ guided_mutate()  ← larger mutation on low-importance genes
    └─ run()                (orchestrate all 3 phases, N outer loops)

GeneImportance
--------------
Per gene, tracks:
  importance_score : Pearson |r| between gene value and fitness across winners
  mean_winner_value: average value of this gene in top-K winners
  std_winner_value : spread of this gene among winners
  keep_confidence  : 1.0 = strongly conserved across winners; 0.0 = scattered

GenePool
--------
Persistent registry of "proven good" gene values accumulated across
multiple evolution runs and top winners.  The pool is updated after
every EvolutionaryEngine.run() call.  It is used by StrategyGenetics
to:
  1. Bias new genomes towards proven gene ranges.
  2. Increase mutation sigma for unimportant genes (explore freely).
  3. Decrease mutation sigma for high-confidence genes (exploit).

MetaLearningResult
------------------
  best_genome       : overall best genome found across all outer loops
  best_fitness      : its fitness metrics
  gene_importances  : Dict[gene_name → GeneImportance]
  gene_insights     : human-readable list of findings (for API)
  outer_loop_bests  : best fitness per outer loop
  evolution_results : List[EvolutionResult] from each outer loop

API endpoints (wired in main.py)
---------------------------------
  POST /api/meta/run           → trigger meta-learning (slow, ≥30s)
  GET  /api/meta/status        → MetaLearningResult.to_dict()
  GET  /api/meta/gene_insights → plain-English findings
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
    EvolutionResult,
    EvolutionaryEngine,
    MarketSimulator,
    _ALL_MODES,
    _MODE_WEIGHT_MIN, _MODE_WEIGHT_MAX,
    _MIN_SCORE_MIN,   _MIN_SCORE_MAX,
    _TP_RR_MIN,       _TP_RR_MAX,
    _SL_ATR_MIN,      _SL_ATR_MAX,
    _WAVE_FLOOR_MIN,  _WAVE_FLOOR_MAX,
    _LOT_SCALE_MIN,   _LOT_SCALE_MAX,
    _MUTATION_RATE, _MUTATION_SIGMA, _ELITISM_N,
)

logger = logging.getLogger(__name__)

# ── Scalar gene names (excluding mode_weights dict) ─────────────────────── #

_SCALAR_GENES: List[str] = [
    "min_score",
    "tp_rr",
    "sl_atr_mult",
    "wave_conf_floor",
    "lot_scale",
]

_SCALAR_BOUNDS: Dict[str, Tuple[float, float]] = {
    "min_score":       (_MIN_SCORE_MIN,  _MIN_SCORE_MAX),
    "tp_rr":           (_TP_RR_MIN,      _TP_RR_MAX),
    "sl_atr_mult":     (_SL_ATR_MIN,     _SL_ATR_MAX),
    "wave_conf_floor": (_WAVE_FLOOR_MIN, _WAVE_FLOOR_MAX),
    "lot_scale":       (_LOT_SCALE_MIN,  _LOT_SCALE_MAX),
}

# Names of ALL genes (scalar + one per mode_weight)
_ALL_GENE_NAMES: List[str] = _SCALAR_GENES + [f"mw_{m}" for m in _ALL_MODES]

# Meta-learning hyper-parameters
_DEFAULT_OUTER_LOOPS   = 3    # evolution runs per meta-learning session
_DEFAULT_TOP_K_WINNERS = 5    # top-K genomes used per run for gene learning
_IMPORTANCE_FLOOR      = 0.05 # |r| below this → gene is "neutral" (no bias)
_HIGH_IMPORTANCE       = 0.50 # |r| above this → gene is "dominant"


# ── GeneImportance ──────────────────────────────────────────────────────── #

@dataclass
class GeneImportance:
    """
    Importance metrics for a single gene, computed from top-K winners.

    importance_score : Pearson |r| between gene value and fitness rank
                       across winner population (0 = no correlation,
                       1 = perfect correlation).
    mean_winner_value: mean gene value in the winner population.
    std_winner_value : spread; low std → conserved gene (high keep confidence).
    keep_confidence  : derived score 0–1 showing how consistently winners
                       converge on this gene's range.
                       keep_confidence = importance × (1 − normalised_std)
    sample_count     : number of winner genomes this is computed from.
    """
    gene_name:         str
    importance_score:  float = 0.0
    mean_winner_value: float = 0.0
    std_winner_value:  float = 0.0
    keep_confidence:   float = 0.0
    sample_count:      int   = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gene_name":         self.gene_name,
            "importance_score":  round(self.importance_score, 4),
            "mean_winner_value": round(self.mean_winner_value, 4),
            "std_winner_value":  round(self.std_winner_value, 4),
            "keep_confidence":   round(self.keep_confidence, 4),
            "sample_count":      self.sample_count,
        }

    def human_label(self) -> str:
        """Return a human-readable importance label."""
        if self.importance_score >= _HIGH_IMPORTANCE:
            return "DOMINANT"
        if self.importance_score >= 0.25:
            return "SIGNIFICANT"
        if self.importance_score >= _IMPORTANCE_FLOOR:
            return "MINOR"
        return "NEUTRAL"


# ── GenePool ────────────────────────────────────────────────────────────── #

class GenePool:
    """
    Persistent registry of proven-good gene values.

    Accumulated across multiple EvolutionaryEngine runs.  After each run
    the top-K winner genomes are fed in via absorb().  The pool learns
    which gene ranges produce winners so that StrategyGenetics can breed
    smarter next-generation agents.

    Internally stores raw (gene_value, fitness) pairs per gene name.
    """

    def __init__(self) -> None:
        # gene_name → list of (value, fitness) tuples
        self._data: Dict[str, List[Tuple[float, float]]] = {
            name: [] for name in _ALL_GENE_NAMES
        }
        self._run_count = 0

    @property
    def run_count(self) -> int:
        return self._run_count

    def absorb(
        self,
        genomes:   List[AgentGenome],
        fitnesses: List[AgentFitness],
    ) -> None:
        """
        Add (genome, fitness) pairs from a completed evolution run.

        Should be called with the top-K winners only to keep the pool
        focused on high-quality gene values.
        """
        for genome, fitness in zip(genomes, fitnesses):
            for gene in _SCALAR_GENES:
                val = float(getattr(genome, gene, 0.0))
                self._data[gene].append((val, fitness.fitness))
            for mode in _ALL_MODES:
                key = f"mw_{mode}"
                val = float(genome.mode_weights.get(mode, 1.0))
                self._data[key].append((val, fitness.fitness))
        self._run_count += 1

    def get_winner_range(self, gene_name: str) -> Tuple[float, float]:
        """
        Return (mean, std) of values for a gene across all winners.

        Falls back to the gene's full range midpoint / range if no data.
        """
        pairs = self._data.get(gene_name, [])
        if not pairs:
            return self._fallback_mean_std(gene_name)
        vals = [v for v, _ in pairs]
        return float(np.mean(vals)), float(np.std(vals))

    def get_fitness_weighted_mean(self, gene_name: str) -> Optional[float]:
        """
        Return fitness-weighted mean of a gene across all winner observations.

        Returns None if no data.
        """
        pairs = self._data.get(gene_name, [])
        if not pairs:
            return None
        vals    = np.array([v for v, _ in pairs])
        weights = np.array([f for _, f in pairs], dtype=float)
        wsum = float(np.sum(weights))
        if wsum < 1e-9:
            return float(np.mean(vals))
        return float(np.dot(vals, weights) / wsum)

    def total_observations(self, gene_name: str) -> int:
        return len(self._data.get(gene_name, []))

    @staticmethod
    def _fallback_mean_std(gene_name: str) -> Tuple[float, float]:
        if gene_name in _SCALAR_BOUNDS:
            lo, hi = _SCALAR_BOUNDS[gene_name]
            return (lo + hi) / 2, (hi - lo) / 4
        return 1.0, 0.3   # mode_weight fallback


# ── WinnerAnalyzer ──────────────────────────────────────────────────────── #

class WinnerAnalyzer:
    """
    Analyses the top-K genomes from an evolution run to learn
    which genes correlate with high fitness.

    Algorithm
    ---------
    For each gene g:
      1. Collect (gene_value, fitness) pairs from top-K winners.
      2. Compute Pearson |r|.
      3. Compute mean and std of gene_value in winners.
      4. keep_confidence = |r| × (1 − normalised_std)
         where normalised_std = std / gene_range.
    """

    def __init__(self, top_k: int = _DEFAULT_TOP_K_WINNERS) -> None:
        self.top_k = top_k

    def analyze(
        self,
        population:   List[AgentGenome],
        fitnesses:    List[AgentFitness],
    ) -> Tuple[Dict[str, GeneImportance], List[AgentGenome], List[AgentFitness]]:
        """
        Analyse population, return (importances, top_genomes, top_fitnesses).

        top_genomes and top_fitnesses contain the top-K by fitness.
        """
        paired = sorted(
            zip(population, fitnesses),
            key=lambda x: x[1].fitness,
            reverse=True,
        )
        k = min(self.top_k, len(paired))
        top_genomes   = [g for g, _ in paired[:k]]
        top_fitnesses = [f for _, f in paired[:k]]

        importances: Dict[str, GeneImportance] = {}
        for gene in _ALL_GENE_NAMES:
            imp = self._compute_gene_importance(gene, top_genomes, top_fitnesses)
            importances[gene] = imp

        return importances, top_genomes, top_fitnesses

    def _compute_gene_importance(
        self,
        gene_name: str,
        genomes:   List[AgentGenome],
        fitnesses: List[AgentFitness],
    ) -> GeneImportance:
        n = len(genomes)
        if n < 2:
            return GeneImportance(gene_name=gene_name, sample_count=n)

        # Extract gene values
        values: List[float] = []
        for g in genomes:
            if gene_name in _SCALAR_GENES:
                values.append(float(getattr(g, gene_name, 0.0)))
            else:
                mode = gene_name[3:]  # strip "mw_"
                values.append(float(g.mode_weights.get(mode, 1.0)))

        fit_scores = [f.fitness for f in fitnesses]

        vals_arr = np.array(values,     dtype=float)
        fits_arr = np.array(fit_scores, dtype=float)

        mean_val = float(np.mean(vals_arr))
        std_val  = float(np.std(vals_arr))

        # Pearson correlation
        importance = 0.0
        if std_val > 1e-9 and float(np.std(fits_arr)) > 1e-9:
            corr = float(np.corrcoef(vals_arr, fits_arr)[0, 1])
            importance = min(abs(corr), 1.0)

        # Normalised std (how conserved is this gene among winners?)
        if gene_name in _SCALAR_BOUNDS:
            lo, hi = _SCALAR_BOUNDS[gene_name]
        else:
            lo, hi = _MODE_WEIGHT_MIN, _MODE_WEIGHT_MAX
        gene_range  = (hi - lo) + 1e-9
        norm_std    = std_val / gene_range
        keep_conf   = importance * max(0.0, 1.0 - norm_std)

        return GeneImportance(
            gene_name         = gene_name,
            importance_score  = round(importance, 4),
            mean_winner_value = round(mean_val,   4),
            std_winner_value  = round(std_val,    4),
            keep_confidence   = round(keep_conf,  4),
            sample_count      = n,
        )


# ── StrategyGenetics ────────────────────────────────────────────────────── #

class StrategyGenetics:
    """
    Genetics-informed breeding engine.

    Uses GeneImportance and GenePool to guide crossover and mutation:
    • High-importance gene → bias towards pool's fitness-weighted mean.
    • Low-importance gene  → larger mutation sigma (free exploration).
    • Breed() produces a child from two parents, biased by gene importance.

    This replaces the naive uniform crossover in EvolutionaryEngine with
    a smarter operator that preserves "what made winners win".
    """

    def __init__(
        self,
        gene_pool:    GenePool,
        importances:  Dict[str, GeneImportance],
        rng:          random.Random,
    ) -> None:
        self._pool       = gene_pool
        self._importance = importances
        self._rng        = rng

    def breed(
        self,
        parent_a: AgentGenome,
        parent_b: AgentGenome,
        gen:      int,
    ) -> AgentGenome:
        """
        Importance-weighted crossover.

        For each gene:
          - With probability = keep_confidence, inherit from the better parent.
          - Otherwise, blend uniformly (standard crossover).
          - Always bias towards pool's fitness-weighted mean at rate α:
              α = importance_score × 0.20   (up to 20% pull towards pool mean)
        """
        def _gene_val(genome: AgentGenome, gene: str) -> float:
            if gene in _SCALAR_GENES:
                return float(getattr(genome, gene, 0.0))
            mode = gene[3:]
            return float(genome.mode_weights.get(mode, 1.0))

        def _blend_gene(gene: str, va: float, vb: float) -> float:
            imp   = self._importance.get(gene, GeneImportance(gene_name=gene))
            kconf = imp.keep_confidence

            # Step 1: choose between high-confidence inheritance vs uniform blend
            if self._rng.random() < kconf:
                # Inherit the better parent's value (exploitation)
                val = va   # parent_a is expected to be the fitter parent
            else:
                lo, hi = min(va, vb), max(va, vb)
                val = self._rng.uniform(lo, hi) if lo < hi else va

            # Step 2: bias towards pool's fitness-weighted mean
            pool_mean = self._pool.get_fitness_weighted_mean(gene)
            if pool_mean is not None:
                alpha = imp.importance_score * 0.20
                val   = (1.0 - alpha) * val + alpha * pool_mean

            # Clamp to bounds
            if gene in _SCALAR_BOUNDS:
                lo_b, hi_b = _SCALAR_BOUNDS[gene]
            else:
                lo_b, hi_b = _MODE_WEIGHT_MIN, _MODE_WEIGHT_MAX
            return max(lo_b, min(hi_b, val))

        # Build child mode_weights
        child_mw = {}
        for mode in _ALL_MODES:
            gname = f"mw_{mode}"
            va    = parent_a.mode_weights.get(mode, 1.0)
            vb    = parent_b.mode_weights.get(mode, 1.0)
            child_mw[mode] = _blend_gene(gname, va, vb)

        return AgentGenome(
            mode_weights    = child_mw,
            min_score       = _blend_gene("min_score",       parent_a.min_score,       parent_b.min_score),
            tp_rr           = _blend_gene("tp_rr",           parent_a.tp_rr,           parent_b.tp_rr),
            sl_atr_mult     = _blend_gene("sl_atr_mult",     parent_a.sl_atr_mult,     parent_b.sl_atr_mult),
            wave_conf_floor = _blend_gene("wave_conf_floor", parent_a.wave_conf_floor, parent_b.wave_conf_floor),
            lot_scale       = _blend_gene("lot_scale",       parent_a.lot_scale,       parent_b.lot_scale),
            generation      = gen,
            parent_ids      = (id(parent_a), id(parent_b)),
        )

    def guided_mutate(self, genome: AgentGenome) -> AgentGenome:
        """
        Importance-guided mutation.

        High-importance gene → small sigma (preserve good values).
        Low-importance gene  → large sigma (explore freely).
        """
        def _mutate_gene(gene: str, val: float) -> float:
            if self._rng.random() >= _MUTATION_RATE:
                return val
            imp = self._importance.get(gene, GeneImportance(gene_name=gene))
            # Invert importance → high importance = small mutation
            sigma_scale = 1.0 - imp.importance_score * 0.70
            if gene in _SCALAR_BOUNDS:
                lo, hi = _SCALAR_BOUNDS[gene]
            else:
                lo, hi = _MODE_WEIGHT_MIN, _MODE_WEIGHT_MAX
            sigma = _MUTATION_SIGMA * sigma_scale * (hi - lo)
            val   = val + self._rng.gauss(0, sigma)
            return max(lo, min(hi, val))

        g = genome.clone()
        for mode in _ALL_MODES:
            g.mode_weights[mode] = _mutate_gene(
                f"mw_{mode}", g.mode_weights.get(mode, 1.0)
            )
        g.min_score       = _mutate_gene("min_score",       g.min_score)
        g.tp_rr           = _mutate_gene("tp_rr",           g.tp_rr)
        g.sl_atr_mult     = _mutate_gene("sl_atr_mult",     g.sl_atr_mult)
        g.wave_conf_floor = _mutate_gene("wave_conf_floor", g.wave_conf_floor)
        g.lot_scale       = _mutate_gene("lot_scale",       g.lot_scale)
        return g


# ── MetaLearningResult ──────────────────────────────────────────────────── #

@dataclass
class MetaLearningResult:
    """Complete result of one MetaLearningEngine.run() call."""
    best_genome:        AgentGenome
    best_fitness:       AgentFitness
    gene_importances:   Dict[str, GeneImportance]   # gene_name → importance
    gene_insights:      List[str]                    # human-readable findings
    outer_loop_bests:   List[float]                  # best fitness per outer loop
    evolution_results:  List[EvolutionResult]
    outer_loops_run:    int
    total_duration_secs: float
    completed_at:       float = field(default_factory=time.time)
    applied_to_live:    bool  = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "best_genome":      self.best_genome.to_dict(),
            "best_fitness":     self.best_fitness.to_dict(),
            "gene_importances": {
                k: v.to_dict() for k, v in self.gene_importances.items()
            },
            "gene_insights":        self.gene_insights,
            "outer_loop_bests":     [round(f, 4) for f in self.outer_loop_bests],
            "outer_loops_run":      self.outer_loops_run,
            "total_duration_secs":  round(self.total_duration_secs, 2),
            "completed_at":         self.completed_at,
            "applied_to_live":      self.applied_to_live,
        }

    def apply_to(self, decision_engine: Any) -> None:
        """
        Apply the best evolved genome to the live DecisionEngine.

        Same mapping as EvolutionResult.apply_to() but uses the meta-learned
        best genome (which has been guided by genetic analysis).
        """
        genome = self.best_genome
        ctrl   = getattr(decision_engine, "controller", None)
        if ctrl is None:
            logger.warning("MetaLearningResult.apply_to: no controller found")
            return

        from .self_play_engine import _ALL_WAVE_STATES
        for mode in _ALL_MODES:
            mw = genome.mode_weights.get(mode, 1.0)
            for wave_state in _ALL_WAVE_STATES:
                key = f"{mode}/{wave_state}"
                adj = max(-0.50, min(0.50, round(mw - 1.0, 3)))
                ctrl._state.mode_weight_adjs[key] = adj

        ctrl.base_min_score = round(max(0.10, min(0.50, genome.min_score)), 3)
        ctrl._state.lot_scale = round(max(0.25, min(1.50, genome.lot_scale)), 3)

        self.applied_to_live = True
        logger.info(
            "MetaLearningResult.apply_to: applied meta-learned genome gen=%d "
            "fitness=%.4f min_score=%.3f lot_scale=%.3f",
            genome.generation,
            self.best_fitness.fitness,
            genome.min_score,
            genome.lot_scale,
        )


# ── MetaLearningEngine ──────────────────────────────────────────────────── #

class MetaLearningEngine:
    """
    META-LEARNING + STRATEGY GENOME ENGINE.

    Turns the evolutionary system into a genetics system:
      Phase 1 — Evolve:        run EvolutionaryEngine to produce winners
      Phase 2 — Analyse:       WinnerAnalyzer learns why they won
      Phase 3 — Accumulate:    GenePool stores proven gene values
      Phase 4 — Breed smarter: StrategyGenetics uses pool to guide next gen
      (repeat N outer loops)

    The key insight: after the first outer loop, breeding is no longer
    random — it is guided by the empirical gene importance computed from
    real winners in simulated markets.

    Parameters
    ----------
    outer_loops       : number of Evolve→Analyse→Breed cycles (default 3)
    pop_size          : agents per evolutionary run (default 20)
    generations       : generations per evolution run (default 5)
    episodes          : market episodes per agent (default 10)
    bars_per_episode  : candle bars per episode (default 80)
    top_k_winners     : how many top agents to learn from per run (default 5)
    seed              : random seed
    """

    def __init__(
        self,
        outer_loops:      int   = _DEFAULT_OUTER_LOOPS,
        pop_size:         int   = 20,
        generations:      int   = 5,
        episodes:         int   = 10,
        bars_per_episode: int   = 80,
        top_k_winners:    int   = _DEFAULT_TOP_K_WINNERS,
        seed:             Optional[int] = None,
    ) -> None:
        self.outer_loops      = outer_loops
        self.pop_size         = pop_size
        self.generations      = generations
        self.episodes         = episodes
        self.bars_per_episode = bars_per_episode
        self.top_k_winners    = top_k_winners
        self.seed             = seed

        self._rng      = random.Random(seed)
        self._gene_pool: GenePool = GenePool()
        self._analyzer: WinnerAnalyzer = WinnerAnalyzer(top_k=top_k_winners)
        self._importances: Dict[str, GeneImportance] = {}
        self._last_result: Optional[MetaLearningResult] = None

    @property
    def gene_pool(self) -> GenePool:
        return self._gene_pool

    @property
    def gene_importances(self) -> Dict[str, GeneImportance]:
        return dict(self._importances)

    @property
    def last_result(self) -> Optional[MetaLearningResult]:
        return self._last_result

    def run(self) -> MetaLearningResult:
        """
        Run the full meta-learning cycle.

        Returns MetaLearningResult with the best genome, gene importances,
        and human-readable insights.
        """
        t0 = time.time()
        logger.info(
            "MetaLearningEngine: starting | outer_loops=%d pop=%d gen=%d eps=%d",
            self.outer_loops, self.pop_size, self.generations, self.episodes,
        )

        outer_bests: List[float] = []
        evo_results: List[EvolutionResult] = []
        overall_best_genome:  AgentGenome  = AgentGenome.default()
        overall_best_fitness: AgentFitness = AgentFitness()

        sim = MarketSimulator(
            episodes=self.episodes,
            bars=self.bars_per_episode,
            seed=self.seed,
        )

        # Seed population for first loop — will be replaced by genetics later
        population: List[AgentGenome] = self._initial_population()

        for loop in range(self.outer_loops):
            logger.info("MetaLearningEngine outer loop %d/%d", loop + 1, self.outer_loops)

            # ── Phase 1: Evolve the current population ─────────────── #
            pop_out, fitnesses, evo_result = self._evolve_population(
                population, sim, loop
            )
            evo_results.append(evo_result)

            # Track global best
            for g, f in zip(pop_out, fitnesses):
                if f.fitness > overall_best_fitness.fitness:
                    overall_best_fitness = f
                    overall_best_genome  = g.clone()
                    overall_best_genome.generation = loop * self.generations

            loop_best = max(f.fitness for f in fitnesses)
            outer_bests.append(loop_best)
            logger.info(
                "MetaLearningEngine loop %d: best_fitness=%.4f",
                loop + 1, loop_best,
            )

            # ── Phase 2: Analyse winners ───────────────────────────── #
            importances, top_genomes, top_fitnesses = self._analyzer.analyze(
                pop_out, fitnesses
            )
            self._importances = importances

            # ── Phase 3: Accumulate in GenePool ───────────────────── #
            self._gene_pool.absorb(top_genomes, top_fitnesses)

            if loop == self.outer_loops - 1:
                break   # last loop: no need to breed

            # ── Phase 4: Breed next generation using genetics ──────── #
            genetics = StrategyGenetics(
                gene_pool   = self._gene_pool,
                importances = importances,
                rng         = self._rng,
            )
            population = self._breed_next_population(
                pop_out, fitnesses, genetics, loop + 1
            )

        duration = time.time() - t0

        # Generate human-readable gene insights
        insights = self._build_gene_insights(self._importances)

        logger.info(
            "MetaLearningEngine: done in %.2fs | best_fitness=%.4f "
            "dominant_genes=%s",
            duration,
            overall_best_fitness.fitness,
            [n for n, i in self._importances.items() if i.human_label() == "DOMINANT"][:3],
        )

        result = MetaLearningResult(
            best_genome         = overall_best_genome,
            best_fitness        = overall_best_fitness,
            gene_importances    = self._importances,
            gene_insights       = insights,
            outer_loop_bests    = outer_bests,
            evolution_results   = evo_results,
            outer_loops_run     = self.outer_loops,
            total_duration_secs = round(duration, 3),
        )
        self._last_result = result
        return result

    # ── Internal helpers ───────────────────────────────────────────────── #

    def _initial_population(self) -> List[AgentGenome]:
        """Create initial population (1 default + rest random)."""
        pop: List[AgentGenome] = [AgentGenome.default()]
        while len(pop) < self.pop_size:
            pop.append(AgentGenome.random(self._rng, gen=0))
        return pop

    def _evolve_population(
        self,
        population: List[AgentGenome],
        sim:        MarketSimulator,
        loop:       int,
    ) -> Tuple[List[AgentGenome], List[AgentFitness], EvolutionResult]:
        """
        Run N generations of evolution on the given population.

        Uses the same tournament selection + crossover + mutation as
        EvolutionaryEngine, but operates on an externally provided starting
        population (which may have been genetics-bred from the previous loop).
        """
        evo_engine = EvolutionaryEngine(
            pop_size         = self.pop_size,
            generations      = self.generations,
            episodes         = self.episodes,
            bars_per_episode = self.bars_per_episode,
            seed             = self._rng.randint(0, 10_000),
        )
        # Inject our starting population (replace the default random init)
        evo_engine._rng = random.Random(self._rng.randint(0, 10_000))

        # Run a stripped-down evolution loop reusing the engine's internals
        gen_bests: List[float] = []
        best_genome:  AgentGenome  = population[0].clone()
        best_fitness: AgentFitness = AgentFitness()
        current_pop = [g.clone() for g in population]

        for gen in range(self.generations):
            fitnesses: List[AgentFitness] = [sim.evaluate(g) for g in current_pop]

            for g, f in zip(current_pop, fitnesses):
                if f.fitness > best_fitness.fitness:
                    best_fitness = f
                    best_genome  = g.clone()
                    best_genome.generation = loop * self.generations + gen

            gen_bests.append(max(f.fitness for f in fitnesses))

            if gen == self.generations - 1:
                break

            ranked = sorted(
                zip(current_pop, fitnesses),
                key=lambda x: x[1].fitness,
                reverse=True,
            )
            next_pop: List[AgentGenome] = []
            for elite, _ in ranked[:_ELITISM_N]:
                cloned = elite.clone()
                cloned.generation = loop * self.generations + gen + 1
                next_pop.append(cloned)
            while len(next_pop) < self.pop_size:
                pa = evo_engine._tournament_select(current_pop, fitnesses)
                pb = evo_engine._tournament_select(current_pop, fitnesses)
                child = evo_engine._crossover(pa, pb, loop * self.generations + gen + 1)
                child = evo_engine._mutate(child)
                next_pop.append(child)
            current_pop = next_pop

        evo_result = EvolutionResult(
            best_genome          = best_genome,
            best_fitness         = best_fitness,
            population_fitnesses = fitnesses,
            generation_bests     = gen_bests,
            generations_run      = self.generations,
            population_size      = self.pop_size,
            episodes_per_agent   = self.episodes,
            duration_secs        = 0.0,
        )
        return current_pop, fitnesses, evo_result

    def _breed_next_population(
        self,
        population: List[AgentGenome],
        fitnesses:  List[AgentFitness],
        genetics:   StrategyGenetics,
        gen:        int,
    ) -> List[AgentGenome]:
        """
        Breed the next outer-loop starting population using StrategyGenetics.

        Elitism: top 2 carry over unchanged.
        Rest: importance-weighted crossover + guided mutation.
        """
        ranked = sorted(
            zip(population, fitnesses),
            key=lambda x: x[1].fitness,
            reverse=True,
        )
        next_pop: List[AgentGenome] = []
        for elite, _ in ranked[:_ELITISM_N]:
            cloned = elite.clone()
            cloned.generation = gen
            next_pop.append(cloned)

        while len(next_pop) < self.pop_size:
            # Tournament select two parents
            indices = self._rng.sample(range(len(population)), min(3, len(population)))
            fit_idx = max(indices, key=lambda i: fitnesses[i].fitness)
            pa = population[fit_idx]

            indices2 = self._rng.sample(range(len(population)), min(3, len(population)))
            fit_idx2 = max(indices2, key=lambda i: fitnesses[i].fitness)
            pb = population[fit_idx2]

            # Better parent first (genetics.breed biases toward parent_a)
            if fitnesses[population.index(pa) if pa in population else 0].fitness < \
               fitnesses[population.index(pb) if pb in population else 0].fitness:
                pa, pb = pb, pa

            child = genetics.breed(pa, pb, gen)
            child = genetics.guided_mutate(child)
            next_pop.append(child)

        return next_pop

    @staticmethod
    def _build_gene_insights(
        importances: Dict[str, GeneImportance],
    ) -> List[str]:
        """
        Build a list of human-readable insights from gene importance analysis.

        These are the answers to "vì sao winner thắng?" and
        "gene nào nên giữ, gene nào nên loại?"
        """
        insights: List[str] = []

        # Sort by importance descending
        ranked = sorted(
            importances.values(),
            key=lambda x: x.importance_score,
            reverse=True,
        )

        dominant   = [i for i in ranked if i.human_label() == "DOMINANT"]
        significant= [i for i in ranked if i.human_label() == "SIGNIFICANT"]
        neutral    = [i for i in ranked if i.human_label() == "NEUTRAL"]

        if dominant:
            names = [i.gene_name for i in dominant]
            insights.append(
                f"DOMINANT genes (keep tightly): {names} — "
                f"these show the strongest correlation with winner fitness."
            )
            for d in dominant[:3]:
                insights.append(
                    f"  • {d.gene_name}: winners average {d.mean_winner_value:.3f} "
                    f"(std={d.std_winner_value:.3f}, keep_conf={d.keep_confidence:.3f})"
                )

        if significant:
            names = [i.gene_name for i in significant]
            insights.append(
                f"SIGNIFICANT genes (moderate importance): {names}"
            )

        if neutral:
            names = [i.gene_name for i in neutral]
            insights.append(
                f"NEUTRAL genes (can be freely mutated/explored): {names}"
            )

        # Special case: high-confidence conserved genes
        conserved = [i for i in ranked if i.keep_confidence > 0.4]
        if conserved:
            for c in conserved[:3]:
                insights.append(
                    f"CONSERVED: {c.gene_name} consistently converges to "
                    f"~{c.mean_winner_value:.3f} across winners "
                    f"(keep_confidence={c.keep_confidence:.3f})"
                )

        # Low-std genes (very consistent across all winners)
        low_std = [
            i for i in ranked
            if i.std_winner_value < 0.05 and i.sample_count >= 3
        ]
        if low_std:
            names = [i.gene_name for i in low_std[:4]]
            insights.append(
                f"HIGHLY CONSISTENT genes (all winners agree): {names} — "
                f"strong signal for the optimal value range."
            )

        if not insights:
            insights.append(
                "Not enough winner data yet to draw gene insights. "
                "Run more outer loops or increase top_k_winners."
            )

        return insights
