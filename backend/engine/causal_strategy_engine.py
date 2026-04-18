"""
World Model + Causal Strategy Engine.

Mục tiêu
--------
Nâng cấp từ **strategy genetics system** → **causal strategic intelligence**:

  hệ không chỉ học gene nào hay thắng (tương quan),
  mà bắt đầu học:
    • gene nào thật sự GÂY RA thắng lợi      → CausalGeneAnalyzer (intervention)
    • điều gì chỉ là tương quan giả           → spurious_score, regime_robustness
    • nếu môi trường đổi, gene nào còn sống  → cross-regime consistency test
    • nếu không có dữ liệu trực tiếp, suy diễn → WorldModel (counterfactual inference)

Architecture
------------
  CausalStrategyEngine
    ├─ Phase 1: DataCollection
    │    └─ Chạy MarketSimulator trên N_SAMPLE genomes × 3 regimes
    │         → (genome_vector, regime_id, fitness) triplets
    │
    ├─ Phase 2: WorldModel
    │    ├─ Fit linear model per regime: fitness = W · gene_vector + b
    │    ├─ Fit global model (all regimes + dummy vars)
    │    ├─ R² quality score per regime
    │    └─ counterfactual_predict(): argmax fitness over gene grid
    │
    ├─ Phase 3: CausalGeneAnalyzer
    │    ├─ intervention_effect():     perturb gene g ± Δ, hold rest fixed, ΔFitness
    │    ├─ cross_regime_consistency(): |r| per regime → min = regime_robustness
    │    ├─ partial_correlation():     residual r after regressing out other genes
    │    └─ spurious_score():          std of per-regime importance (high = spurious)
    │
    ├─ Phase 4: Synthesis
    │    ├─ CausalScoreCard per gene
    │    └─ CounterfactualGenome from WorldModel's optimal predictions
    │
    └─ CausalIntelligenceResult
         ├─ causal_scorecards: Dict[gene → CausalScoreCard]
         ├─ counterfactual_genome: AgentGenome (causally optimal)
         ├─ world_model_r2: Dict[regime → R²]
         ├─ causal_insights: List[str] (human-readable)
         └─ apply_to(decision_engine)

CausalScoreCard (per gene)
--------------------------
  causal_score        : mean |ΔFitness| per unit ΔGene from intervention trials
  spurious_score      : std of correlation across regimes (high = regime-specific)
  regime_robustness   : min |r| across all regimes (low = dies in some regimes)
  partial_corr        : partial Pearson r (controlling for all other genes)
  counterfactual_value: WorldModel-inferred optimal value for this gene
  is_causal           : causal_score > threshold AND regime_robustness > threshold
  is_spurious         : high raw correlation but high spurious_score

WorldModel (pure numpy, no external ML)
-----------------------------------------
  Per regime: fit_with_lstsq(X, y) where X = [gene_vector | 1.0]
  Global:     X = [gene_vector | regime_dummies | 1.0]
  Counterfactual: for each gene, grid-search optimal value holding others fixed

CausalGeneAnalyzer (ablation + partial correlation)
----------------------------------------------------
  For each gene g:
    1. Sample M base genomes from the data collection pool
    2. Perturb g by ±0.3 × gene_range, evaluate on simulator
    3. causal_score = mean |ΔFitness / ΔGene|
    4. Partial r: regress both gene_values and fitness on all other genes; r of residuals

API endpoints (wired in main.py)
---------------------------------
  POST /api/causal/run     → trigger full causal analysis
  GET  /api/causal/status  → CausalIntelligenceResult.to_dict()
  GET  /api/causal/insights→ plain-English causal findings
  POST /api/causal/apply   → apply counterfactual genome to live system
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
    _MODE_WEIGHT_MIN, _MODE_WEIGHT_MAX,
    _MIN_SCORE_MIN,   _MIN_SCORE_MAX,
    _TP_RR_MIN,       _TP_RR_MAX,
    _SL_ATR_MIN,      _SL_ATR_MAX,
    _WAVE_FLOOR_MIN,  _WAVE_FLOOR_MAX,
    _LOT_SCALE_MIN,   _LOT_SCALE_MAX,
)
from .synthetic_engine import _ALL_WAVE_STATES

logger = logging.getLogger(__name__)

# ── Gene metadata ─────────────────────────────────────────────────────────── #

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

_ALL_GENE_NAMES: List[str] = _SCALAR_GENES + [f"mw_{m}" for m in _ALL_MODES]

# Causal hyper-parameters
_DEFAULT_N_SAMPLE         = 30    # genomes sampled for data collection
_DEFAULT_REGIMES          = list(_ALL_WAVE_STATES)  # BULL_MAIN, BEAR_MAIN, SIDEWAYS
_DEFAULT_INTERVENTION_M   = 8     # base genomes used per intervention trial
_INTERVENTION_DELTA_FRAC  = 0.30  # perturbation = delta_frac × gene_range
_CAUSAL_SCORE_THRESHOLD   = 0.05  # min causal_score to be "causal"
_ROBUSTNESS_THRESHOLD     = 0.05  # min regime_robustness to be "causal"
_SPURIOUS_THRESHOLD       = 0.35  # spurious_score above this → "spurious"
_COUNTERFACTUAL_STEPS     = 20    # grid steps per gene for counterfactual search


def _gene_val(genome: AgentGenome, gene: str) -> float:
    """Extract a single gene value from a genome."""
    if gene in _SCALAR_GENES:
        return float(getattr(genome, gene, 0.0))
    mode = gene[3:]   # strip "mw_"
    return float(genome.mode_weights.get(mode, 1.0))


def _gene_bounds(gene: str) -> Tuple[float, float]:
    if gene in _SCALAR_BOUNDS:
        return _SCALAR_BOUNDS[gene]
    return _MODE_WEIGHT_MIN, _MODE_WEIGHT_MAX


def _set_gene(genome: AgentGenome, gene: str, value: float) -> AgentGenome:
    """Return a clone of genome with gene set to value."""
    g = genome.clone()
    if gene in _SCALAR_GENES:
        setattr(g, gene, value)
    else:
        mode = gene[3:]
        g.mode_weights[mode] = value
    return g


def _genome_to_vector(genome: AgentGenome) -> np.ndarray:
    """Flatten all genes to a numpy vector (same order as _ALL_GENE_NAMES)."""
    return np.array([_gene_val(genome, g) for g in _ALL_GENE_NAMES], dtype=float)


def _vector_to_genome(vec: np.ndarray, gen: int = 0) -> AgentGenome:
    """Reconstruct an AgentGenome from a gene vector."""
    assert len(vec) == len(_ALL_GENE_NAMES)
    mw = {}
    for mode in _ALL_MODES:
        idx = _ALL_GENE_NAMES.index(f"mw_{mode}")
        lo, hi = _MODE_WEIGHT_MIN, _MODE_WEIGHT_MAX
        mw[mode] = float(max(lo, min(hi, vec[idx])))
    scalar_vals = {}
    for gene in _SCALAR_GENES:
        idx = _ALL_GENE_NAMES.index(gene)
        lo, hi = _gene_bounds(gene)
        scalar_vals[gene] = float(max(lo, min(hi, vec[idx])))
    return AgentGenome(
        mode_weights    = mw,
        min_score       = scalar_vals["min_score"],
        tp_rr           = scalar_vals["tp_rr"],
        sl_atr_mult     = scalar_vals["sl_atr_mult"],
        wave_conf_floor = scalar_vals["wave_conf_floor"],
        lot_scale       = scalar_vals["lot_scale"],
        generation      = gen,
    )


# ── CausalScoreCard ──────────────────────────────────────────────────────── #

@dataclass
class CausalScoreCard:
    """
    Causal analysis results for one gene.

    causal_score        : mean |ΔFitness / ΔGene| from intervention trials.
                          High → gene actually causes fitness changes.
    spurious_score      : std of per-regime correlation. High → correlation
                          only holds in one regime (regime-specific).
    regime_robustness   : min |Pearson r| across regimes. High → gene works
                          across all market conditions.
    partial_corr        : partial correlation with fitness (after removing
                          variance explained by all other genes).
    counterfactual_value: WorldModel's predicted optimal gene value.
    per_regime_corr     : Dict[regime → raw Pearson r] for transparency.
    is_causal           : causal_score > threshold (proven by intervention).
    is_spurious         : high spurious_score with some raw correlation
                          (regime-specific effect, not universal).
    is_regime_sensitive : is_causal but low regime_robustness (works in
                          some regimes but not all — still useful, but
                          regime-aware deployment recommended).
    sample_count        : number of data points used
    """
    gene_name:             str
    causal_score:          float = 0.0
    spurious_score:        float = 0.0
    regime_robustness:     float = 0.0
    partial_corr:          float = 0.0
    counterfactual_value:  Optional[float] = None
    per_regime_corr:       Dict[str, float] = field(default_factory=dict)
    is_causal:             bool = False
    is_spurious:           bool = False
    is_regime_sensitive:   bool = False
    sample_count:          int  = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gene_name":             self.gene_name,
            "causal_score":          round(self.causal_score,      4),
            "spurious_score":        round(self.spurious_score,    4),
            "regime_robustness":     round(self.regime_robustness, 4),
            "partial_corr":          round(self.partial_corr,      4),
            "counterfactual_value": (
                round(self.counterfactual_value, 4)
                if self.counterfactual_value is not None else None
            ),
            "per_regime_corr": {
                k: round(v, 4) for k, v in self.per_regime_corr.items()
            },
            "is_causal":           self.is_causal,
            "is_spurious":         self.is_spurious,
            "is_regime_sensitive": self.is_regime_sensitive,
            "sample_count":        self.sample_count,
        }

    def causal_label(self) -> str:
        if self.is_causal and not self.is_regime_sensitive:
            return "CAUSAL"          # causal + works across all regimes
        if self.is_causal and self.is_regime_sensitive:
            return "CAUSAL_SENSITIVE"  # causal but regime-dependent
        if self.is_spurious:
            return "SPURIOUS"
        if self.regime_robustness > _ROBUSTNESS_THRESHOLD:
            return "CORRELATED"
        return "NEUTRAL"


# ── WorldModel ────────────────────────────────────────────────────────────── #

class WorldModel:
    """
    Linear World Model: P(fitness | gene_vector, regime).

    Fits a separate OLS model per regime and a combined global model with
    regime dummies.  Uses np.linalg.lstsq — no external ML library needed.

    Counterfactual inference: for each gene, grid-search over its range
    while holding all other genes at baseline, return argmax predicted fitness.

    This answers: "if we could set gene X to anything, what value would the
    model predict maximises fitness?"
    """

    def __init__(self) -> None:
        # Per-regime model: {regime → (weights, bias)}
        self._per_regime: Dict[str, Tuple[np.ndarray, float]] = {}
        # Global model: (weights for [genes | regime_dummies], bias)
        self._global_w: Optional[np.ndarray] = None
        self._global_b: float = 0.0
        self._r2: Dict[str, float] = {}
        self._global_r2: float = 0.0
        self._n_genes: int = len(_ALL_GENE_NAMES)
        self._regimes: List[str] = list(_DEFAULT_REGIMES)
        self._fitted: bool = False

    @property
    def is_fitted(self) -> bool:
        return self._fitted

    @property
    def r2_scores(self) -> Dict[str, float]:
        return dict(self._r2)

    @property
    def global_r2(self) -> float:
        return self._global_r2

    def fit(
        self,
        genomes:  List[AgentGenome],
        regimes:  List[str],
        fitnesses: List[float],
    ) -> None:
        """
        Fit the world model on collected (genome, regime, fitness) triplets.

        Each row: genome_vector × regime → fitness.
        """
        vectors = np.array([_genome_to_vector(g) for g in genomes], dtype=float)
        y       = np.array(fitnesses, dtype=float)

        # ── Per-regime models ──────────────────────────────────────────── #
        for regime in self._regimes:
            mask = np.array([r == regime for r in regimes])
            if mask.sum() < 3:
                continue
            X_r = vectors[mask]
            y_r = y[mask]
            w, b, r2 = self._fit_ols(X_r, y_r)
            self._per_regime[regime] = (w, b)
            self._r2[regime] = r2
            logger.debug("WorldModel regime=%s R²=%.3f n=%d", regime, r2, mask.sum())

        # ── Global model with regime dummies ──────────────────────────── #
        n_regimes = len(self._regimes)
        regime_dummies = np.zeros((len(genomes), n_regimes), dtype=float)
        for i, r in enumerate(regimes):
            if r in self._regimes:
                regime_dummies[i, self._regimes.index(r)] = 1.0
        X_global = np.hstack([vectors, regime_dummies])
        w_g, b_g, r2_g = self._fit_ols(X_global, y)
        self._global_w   = w_g
        self._global_b   = b_g
        self._global_r2  = r2_g
        self._fitted = True
        logger.info(
            "WorldModel fitted: global_R²=%.3f per_regime=%s",
            r2_g,
            {k: round(v, 3) for k, v in self._r2.items()},
        )

    def predict(
        self,
        genome: AgentGenome,
        regime: str = "BULL_MAIN",
    ) -> float:
        """
        Predict fitness for a genome in a given regime.

        Uses per-regime model if available, otherwise global model.
        """
        vec = _genome_to_vector(genome)
        if regime in self._per_regime:
            w, b = self._per_regime[regime]
            return float(np.dot(vec, w) + b)
        if self._global_w is not None:
            n_regimes = len(self._regimes)
            dummy = np.zeros(n_regimes)
            if regime in self._regimes:
                dummy[self._regimes.index(regime)] = 1.0
            x = np.concatenate([vec, dummy])
            return float(np.dot(x, self._global_w) + self._global_b)
        return 0.0

    def counterfactual_value(
        self,
        gene:     str,
        baseline: AgentGenome,
        regimes:  Optional[List[str]] = None,
    ) -> float:
        """
        Return the gene value that maximises average predicted fitness across regimes,
        holding all other genes fixed at baseline.

        This answers the counterfactual: "what should gene X be?"
        """
        if not self._fitted:
            lo, hi = _gene_bounds(gene)
            return (lo + hi) / 2.0

        regimes = regimes or self._regimes
        lo, hi  = _gene_bounds(gene)
        grid    = np.linspace(lo, hi, _COUNTERFACTUAL_STEPS)

        best_val   = float(_gene_val(baseline, gene))
        best_score = -1e9

        for candidate in grid:
            modified = _set_gene(baseline, gene, float(candidate))
            avg_pred = float(np.mean([
                self.predict(modified, r) for r in regimes
            ]))
            if avg_pred > best_score:
                best_score = avg_pred
                best_val   = float(candidate)

        lo_b, hi_b = _gene_bounds(gene)
        return max(lo_b, min(hi_b, best_val))

    # ── Internal ──────────────────────────────────────────────────────── #

    @staticmethod
    def _fit_ols(
        X: np.ndarray,
        y: np.ndarray,
    ) -> Tuple[np.ndarray, float, float]:
        """
        OLS via np.linalg.lstsq.

        Returns (weights, bias, R²).
        """
        n = len(y)
        X_aug = np.hstack([X, np.ones((n, 1))])
        result, _, _, _ = np.linalg.lstsq(X_aug, y, rcond=None)
        weights = result[:-1]
        bias    = float(result[-1])
        y_pred  = X_aug @ result
        ss_res  = float(np.sum((y - y_pred) ** 2))
        ss_tot  = float(np.sum((y - np.mean(y)) ** 2))
        r2      = max(0.0, 1.0 - ss_res / (ss_tot + 1e-9))
        return weights, bias, round(r2, 4)


# ── CausalGeneAnalyzer ───────────────────────────────────────────────────── #

class CausalGeneAnalyzer:
    """
    Computes causal scores for each gene using three complementary methods:

    1. **Intervention effect** (ablation):
       Perturb gene g by ±Δ (holding all others constant), measure ΔFitness.
       causal_score = mean |ΔFitness / ΔGene| across M base genomes.

    2. **Cross-regime consistency** (spuriousness detection):
       Compute Pearson |r| between gene g and fitness separately for each
       market regime.  If correlation only holds in one regime → spurious.
       regime_robustness = min |r| across regimes.
       spurious_score = std of per-regime |r| values.

    3. **Partial correlation** (controlling for other genes):
       Regress gene_g and fitness on all other genes; partial r = Pearson r
       of the resulting residuals.

    All three methods together give a cleaner picture of causation vs correlation.
    """

    def __init__(
        self,
        sim:              MarketSimulator,
        intervention_m:   int = _DEFAULT_INTERVENTION_M,
    ) -> None:
        self._sim           = sim
        self._intervention_m = intervention_m

    def analyze(
        self,
        genomes:            List[AgentGenome],
        regime_labels:      List[str],
        fitness_per_regime: Dict[str, List[float]],
        all_fitnesses:      List[float],
    ) -> Dict[str, CausalScoreCard]:
        """
        Run full causal analysis on the data-collection pool.

        Parameters
        ----------
        genomes            : all sampled genomes (same order across all lists)
        regime_labels      : regime used to produce each fitness in all_fitnesses
        fitness_per_regime : Dict[regime → list of fitnesses, one per genome]
        all_fitnesses      : fitness for each genome averaged across regimes

        Returns Dict[gene_name → CausalScoreCard].
        """
        n = len(genomes)
        vectors = np.array([_genome_to_vector(g) for g in genomes], dtype=float)
        y_all   = np.array(all_fitnesses, dtype=float)

        # Pre-compute per-regime fitness arrays
        regime_y: Dict[str, np.ndarray] = {
            r: np.array(fitness_per_regime.get(r, all_fitnesses), dtype=float)
            for r in _DEFAULT_REGIMES
        }

        cards: Dict[str, CausalScoreCard] = {}
        for gene_idx, gene in enumerate(_ALL_GENE_NAMES):
            card = self._analyze_gene(
                gene, gene_idx, genomes, vectors, y_all, regime_y, n
            )
            cards[gene] = card

        return cards

    def _analyze_gene(
        self,
        gene:       str,
        gene_idx:   int,
        genomes:    List[AgentGenome],
        vectors:    np.ndarray,
        y_all:      np.ndarray,
        regime_y:   Dict[str, np.ndarray],
        n:          int,
    ) -> CausalScoreCard:

        gene_vals = vectors[:, gene_idx]

        # 1. Per-regime correlation
        per_regime_r: Dict[str, float] = {}
        for regime, ry in regime_y.items():
            if len(ry) < 3:
                per_regime_r[regime] = 0.0
                continue
            std_g = float(np.std(gene_vals))
            std_f = float(np.std(ry))
            if std_g < 1e-9 or std_f < 1e-9:
                per_regime_r[regime] = 0.0
            else:
                per_regime_r[regime] = float(np.corrcoef(gene_vals, ry)[0, 1])

        abs_regime_r = [abs(v) for v in per_regime_r.values()]
        regime_robustness = float(min(abs_regime_r)) if abs_regime_r else 0.0
        spurious_score    = float(np.std(abs_regime_r)) if len(abs_regime_r) > 1 else 0.0

        # 2. Partial correlation (controlling for all other genes)
        partial_r = self._partial_correlation(gene_idx, vectors, y_all)

        # 3. Intervention effect
        causal_score = self._intervention_effect(gene, genomes, n)

        # 4. Counterfactual value via WorldModel (filled in later by engine)
        # Set to None here; WorldModel fills it after fitting.

        # is_causal: proven by intervention effect alone
        is_causal = causal_score > _CAUSAL_SCORE_THRESHOLD

        # is_regime_sensitive: causal but doesn't work in all regimes
        is_regime_sensitive = is_causal and regime_robustness < _ROBUSTNESS_THRESHOLD

        # is_spurious: some correlation but highly regime-specific (likely false correlation)
        is_spurious = (
            spurious_score > _SPURIOUS_THRESHOLD
            and not is_causal          # truly spurious only if intervention says low causal
        ) and sum(abs_regime_r) > 0.10  # must have some correlation to be called spurious

        return CausalScoreCard(
            gene_name            = gene,
            causal_score         = round(causal_score,      4),
            spurious_score       = round(spurious_score,    4),
            regime_robustness    = round(regime_robustness, 4),
            partial_corr         = round(partial_r,         4),
            per_regime_corr      = {k: round(v, 4) for k, v in per_regime_r.items()},
            is_causal            = is_causal,
            is_spurious          = is_spurious,
            is_regime_sensitive  = is_regime_sensitive,
            sample_count         = n,
        )

    def _intervention_effect(
        self,
        gene:    str,
        genomes: List[AgentGenome],
        n:       int,
    ) -> float:
        """
        Estimate causal effect of gene g via intervention (ablation).

        For M randomly selected base genomes:
          1. Evaluate base genome → fitness_base
          2. Perturb gene g by +Δ → evaluate → fitness_plus
          3. Perturb gene g by -Δ → evaluate → fitness_minus
          4. effect = (|fitness_plus - fitness_base| + |fitness_minus - fitness_base|)
                      / (2 × Δ × gene_range)

        Returns mean effect across M base genomes.
        """
        lo, hi    = _gene_bounds(gene)
        gene_range = hi - lo + 1e-9
        delta      = _INTERVENTION_DELTA_FRAC * gene_range

        m = min(self._intervention_m, n)
        # Sample M evenly spaced indices for reproducibility
        step    = max(1, n // m)
        indices = list(range(0, n, step))[:m]

        effects: List[float] = []
        for idx in indices:
            base_genome = genomes[idx]
            base_val    = _gene_val(base_genome, gene)

            # Plus perturbation
            plus_val  = min(hi, base_val + delta)
            plus_g    = _set_gene(base_genome, gene, plus_val)
            fit_plus  = self._sim.evaluate(plus_g).fitness

            # Minus perturbation
            minus_val = max(lo, base_val - delta)
            minus_g   = _set_gene(base_genome, gene, minus_val)
            fit_minus = self._sim.evaluate(minus_g).fitness

            # Base fitness
            fit_base = self._sim.evaluate(base_genome).fitness

            actual_delta = ((plus_val - base_val) + (base_val - minus_val)) / 2 + 1e-9
            effect = (abs(fit_plus - fit_base) + abs(fit_minus - fit_base)) / (
                2 * actual_delta
            )
            effects.append(effect)

        return float(np.mean(effects)) if effects else 0.0

    @staticmethod
    def _partial_correlation(
        target_idx: int,
        X:          np.ndarray,   # shape (n, n_genes)
        y:          np.ndarray,   # shape (n,)
    ) -> float:
        """
        Partial Pearson correlation between X[:, target_idx] and y,
        after partialling out all other columns of X.

        Procedure (Frisch-Waugh):
          1. Regress X[:, target_idx] on X_other → residuals e_x
          2. Regress y on X_other                → residuals e_y
          3. partial_r = Pearson(e_x, e_y)
        """
        n, p = X.shape
        if n < p + 2:
            # Not enough data for partial regression; fall back to simple r
            g_vals = X[:, target_idx]
            std_g = float(np.std(g_vals))
            std_y = float(np.std(y))
            if std_g < 1e-9 or std_y < 1e-9:
                return 0.0
            return float(np.corrcoef(g_vals, y)[0, 1])

        # Columns of X excluding target
        other_cols = [j for j in range(p) if j != target_idx]
        X_other    = X[:, other_cols]
        X_aug      = np.hstack([X_other, np.ones((n, 1))])

        # Residual of target gene on other genes
        g_vals = X[:, target_idx]
        coef_x, _, _, _ = np.linalg.lstsq(X_aug, g_vals, rcond=None)
        e_x = g_vals - X_aug @ coef_x

        # Residual of fitness on other genes
        coef_y, _, _, _ = np.linalg.lstsq(X_aug, y, rcond=None)
        e_y = y - X_aug @ coef_y

        std_x = float(np.std(e_x))
        std_y = float(np.std(e_y))
        if std_x < 1e-9 or std_y < 1e-9:
            return 0.0
        return float(np.corrcoef(e_x, e_y)[0, 1])


# ── CausalIntelligenceResult ─────────────────────────────────────────────── #

@dataclass
class CausalIntelligenceResult:
    """Complete output of CausalStrategyEngine.run()."""
    causal_scorecards:     Dict[str, CausalScoreCard]
    counterfactual_genome: AgentGenome          # world-model-optimal strategy
    world_model_r2:        Dict[str, float]     # R² per regime + "global"
    causal_insights:       List[str]
    n_samples:             int
    duration_secs:         float
    completed_at:          float = field(default_factory=time.time)
    applied_to_live:       bool  = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "causal_scorecards":     {
                k: v.to_dict() for k, v in self.causal_scorecards.items()
            },
            "counterfactual_genome": self.counterfactual_genome.to_dict(),
            "world_model_r2":        {
                k: round(v, 4) for k, v in self.world_model_r2.items()
            },
            "causal_insights":  self.causal_insights,
            "n_samples":        self.n_samples,
            "duration_secs":    round(self.duration_secs, 2),
            "completed_at":     self.completed_at,
            "applied_to_live":  self.applied_to_live,
        }

    def apply_to(self, decision_engine: Any) -> None:
        """Apply the counterfactual (causally optimal) genome to live system."""
        genome = self.counterfactual_genome
        ctrl   = getattr(decision_engine, "controller", None)
        if ctrl is None:
            logger.warning("CausalIntelligenceResult.apply_to: no controller found")
            return

        for mode in _ALL_MODES:
            mw = genome.mode_weights.get(mode, 1.0)
            for wave_state in _ALL_WAVE_STATES:
                key = f"{mode}/{wave_state}"
                adj = max(-0.50, min(0.50, round(mw - 1.0, 3)))
                ctrl._state.mode_weight_adjs[key] = adj

        ctrl.base_min_score   = round(max(0.10, min(0.50, genome.min_score)),   3)
        ctrl._state.lot_scale = round(max(0.25, min(1.50, genome.lot_scale)), 3)

        self.applied_to_live = True
        logger.info(
            "CausalIntelligenceResult.apply_to: applied counterfactual genome "
            "min_score=%.3f lot_scale=%.3f",
            genome.min_score,
            genome.lot_scale,
        )


# ── CausalStrategyEngine ─────────────────────────────────────────────────── #

class CausalStrategyEngine:
    """
    WORLD MODEL + CAUSAL STRATEGY ENGINE.

    The highest layer of the intelligence stack:
      Phase 1 — Data Collection: sample N genomes × 3 regimes → (X, regime, y)
      Phase 2 — World Model: fit linear P(fitness | genome, regime)
      Phase 3 — Causal Analysis: intervention + cross-regime + partial correlation
      Phase 4 — Counterfactual Genome: world model argmax per gene
      Phase 5 — Synthesis: CausalScoreCards + insights

    Key questions answered
    ----------------------
    • gene nào thật sự GÂY RA thắng (not just correlated)?
      → causal_score from intervention ablation
    • điều gì chỉ là tương quan giả?
      → spurious_score from regime-specific correlation
    • nếu môi trường đổi, gene nào còn sống?
      → regime_robustness = min |r| across BULL/BEAR/SIDEWAYS
    • nếu không có dữ liệu trực tiếp, hệ có thể suy diễn chiến lược nào hợp lý?
      → WorldModel counterfactual inference

    Parameters
    ----------
    n_samples        : genomes sampled for data collection (default 30)
    episodes         : market episodes per evaluation (default 8)
    bars_per_episode : candle bars per episode (default 70)
    intervention_m   : base genomes used per intervention trial (default 8)
    seed             : random seed
    """

    def __init__(
        self,
        n_samples:        int   = _DEFAULT_N_SAMPLE,
        episodes:         int   = 8,
        bars_per_episode: int   = 70,
        intervention_m:   int   = _DEFAULT_INTERVENTION_M,
        seed:             Optional[int] = None,
    ) -> None:
        self.n_samples        = n_samples
        self.episodes         = episodes
        self.bars_per_episode = bars_per_episode
        self.intervention_m   = intervention_m
        self.seed             = seed

        self._rng   = random.Random(seed)
        self._world_model: WorldModel = WorldModel()
        self._last_result: Optional[CausalIntelligenceResult] = None

    @property
    def world_model(self) -> WorldModel:
        return self._world_model

    @property
    def last_result(self) -> Optional[CausalIntelligenceResult]:
        return self._last_result

    def run(self) -> CausalIntelligenceResult:
        """
        Execute the full causal intelligence pipeline.

        Returns CausalIntelligenceResult with causal scorecards,
        counterfactual genome, world model quality, and human insights.
        """
        t0 = time.time()
        logger.info(
            "CausalStrategyEngine: starting | n_samples=%d episodes=%d bars=%d",
            self.n_samples, self.episodes, self.bars_per_episode,
        )

        sim = MarketSimulator(
            episodes=self.episodes,
            bars=self.bars_per_episode,
            seed=self.seed,
        )

        # ── Phase 1: Data Collection ───────────────────────────────────── #
        genomes = self._sample_genomes()
        genomes_flat, regime_labels, fitness_flat, fitness_per_regime, avg_fitnesses = \
            self._collect_data(genomes, sim)

        logger.info(
            "CausalStrategyEngine: data collected | n=%d unique_genomes=%d",
            len(genomes_flat), len(genomes),
        )

        # ── Phase 2: World Model ───────────────────────────────────────── #
        wm = WorldModel()
        wm.fit(genomes_flat, regime_labels, fitness_flat)
        self._world_model = wm

        # ── Phase 3: Causal Gene Analysis ─────────────────────────────── #
        analyzer = CausalGeneAnalyzer(
            sim            = sim,
            intervention_m = self.intervention_m,
        )
        scorecards = analyzer.analyze(
            genomes           = genomes,
            regime_labels     = [r for r in regime_labels[:len(genomes)]],
            fitness_per_regime= fitness_per_regime,
            all_fitnesses     = avg_fitnesses,
        )

        # ── Phase 4: Fill counterfactual values via WorldModel ─────────── #
        baseline = self._best_genome(genomes, avg_fitnesses)
        for gene, card in scorecards.items():
            cfv = wm.counterfactual_value(gene, baseline)
            card.counterfactual_value = round(cfv, 4)

        # ── Phase 5: Build counterfactual genome ───────────────────────── #
        cf_genome = self._build_counterfactual_genome(scorecards, baseline, wm)

        # ── Phase 6: Generate insights ─────────────────────────────────── #
        insights = self._build_causal_insights(scorecards, wm)

        duration = time.time() - t0
        r2_dict  = {**wm.r2_scores, "global": wm.global_r2}

        logger.info(
            "CausalStrategyEngine: done in %.2fs | causal_genes=%s spurious_genes=%s",
            duration,
            [n for n, c in scorecards.items() if c.is_causal][:4],
            [n for n, c in scorecards.items() if c.is_spurious][:4],
        )

        result = CausalIntelligenceResult(
            causal_scorecards     = scorecards,
            counterfactual_genome = cf_genome,
            world_model_r2        = r2_dict,
            causal_insights       = insights,
            n_samples             = self.n_samples,
            duration_secs         = round(duration, 3),
        )
        self._last_result = result
        return result

    # ── Internal helpers ───────────────────────────────────────────────── #

    def _sample_genomes(self) -> List[AgentGenome]:
        """
        Build a diverse genome sample: 1 default + N-1 random.

        Diversity is important so the WorldModel can learn meaningful
        variance across the gene space.
        """
        pop: List[AgentGenome] = [AgentGenome.default()]
        while len(pop) < self.n_samples:
            pop.append(AgentGenome.random(self._rng))
        return pop

    def _collect_data(
        self,
        genomes: List[AgentGenome],
        sim:     MarketSimulator,
    ) -> Tuple[
        List[AgentGenome],   # genomes_flat (repeated once per regime)
        List[str],           # regime_labels (one per row in genomes_flat)
        List[float],         # fitness_flat
        Dict[str, List[float]],  # per-regime fitness (one value per genome)
        List[float],             # avg fitness per genome across regimes
    ]:
        """
        Evaluate each genome on each regime separately.

        Returns expanded lists suitable for WorldModel fitting:
          - genomes_flat: each genome repeated len(regimes) times
          - regime_labels: matching regime string per row
          - fitness_flat: matching fitness per row

        And compact lists for CausalGeneAnalyzer:
          - fitness_per_regime: {regime → [fitness_for_genome_i]}
          - avg_fitnesses: mean across regimes per genome
        """
        fitness_per_regime: Dict[str, List[float]] = {r: [] for r in _DEFAULT_REGIMES}

        # Use single-regime simulator per call to isolate regime effects
        single_sim = MarketSimulator(
            episodes = max(1, self.episodes // len(_DEFAULT_REGIMES)),
            bars     = self.bars_per_episode,
            seed     = self.seed,
        )

        for genome in genomes:
            for regime in _DEFAULT_REGIMES:
                # Evaluate genome using only this regime's wave mix
                fit = self._evaluate_single_regime(genome, single_sim, regime)
                fitness_per_regime[regime].append(fit)

        # Build flat and avg lists
        genomes_flat:  List[AgentGenome] = []
        regime_labels: List[str]         = []
        fitness_flat:  List[float]       = []
        avg_fitnesses: List[float]       = []

        for i, genome in enumerate(genomes):
            per_reg = [fitness_per_regime[r][i] for r in _DEFAULT_REGIMES]
            avg_fitnesses.append(float(np.mean(per_reg)))
            for regime in _DEFAULT_REGIMES:
                genomes_flat.append(genome)
                regime_labels.append(regime)
                fitness_flat.append(fitness_per_regime[regime][i])

        return genomes_flat, regime_labels, fitness_flat, fitness_per_regime, avg_fitnesses

    @staticmethod
    def _evaluate_single_regime(
        genome: AgentGenome,
        sim:    MarketSimulator,
        regime: str,
    ) -> float:
        """
        Run a genome through episodes of a specific regime only.

        We temporarily override the wave_mix in sim.evaluate() by running
        a single episode per the target regime.
        """
        # Use sim directly but override the wave mix by running low-level evaluation
        # The MarketSimulator doesn't expose regime-per-episode control directly,
        # so we call its internal _simulate_episode with a regime-specific episode.
        gen = sim._gen
        bars = sim.bars

        pnls:  list = []
        rrs:   list = []
        wins:  list = []

        n_episodes = sim.episodes
        equity = [1.0]

        for _ in range(n_episodes):
            df = gen.generate(regime, n_candles=bars + 10)
            ep_pnls, ep_rrs, ep_wins = sim._simulate_episode(genome, df, regime)
            pnls.extend(ep_pnls)
            rrs.extend(ep_rrs)
            wins.extend(ep_wins)
            eq = equity[-1]
            for p in ep_pnls:
                eq = eq * (1.0 + p * sim._RISK_PER_TRADE)
            equity.append(max(eq, 1e-6))

        return sim._compute_fitness(pnls, rrs, wins, equity).fitness

    @staticmethod
    def _best_genome(
        genomes:   List[AgentGenome],
        fitnesses: List[float],
    ) -> AgentGenome:
        """Return the genome with the highest average fitness."""
        best_idx = int(np.argmax(fitnesses))
        return genomes[best_idx].clone()

    @staticmethod
    def _build_counterfactual_genome(
        scorecards: Dict[str, CausalScoreCard],
        baseline:   AgentGenome,
        wm:         WorldModel,
    ) -> AgentGenome:
        """
        Build a genome by combining world-model-optimal gene values.

        For each gene, use the counterfactual value if the world model
        is reliable (global R² > 0.05).  Otherwise, keep the baseline value.

        Only update genes where the world model has enough signal; for
        genes with no causal signal and low world model confidence, keep
        the baseline to avoid hallucinating random values.
        """
        g = baseline.clone()
        wm_reliable = wm.global_r2 > 0.05

        for gene, card in scorecards.items():
            cfv = card.counterfactual_value
            if cfv is None:
                continue
            # Use counterfactual if: world model is reliable OR gene is causal
            if wm_reliable or card.is_causal:
                lo, hi = _gene_bounds(gene)
                cfv_clamped = max(lo, min(hi, cfv))
                g = _set_gene(g, gene, cfv_clamped)

        g.generation = -1   # mark as world-model-derived
        return g

    @staticmethod
    def _build_causal_insights(
        scorecards: Dict[str, CausalScoreCard],
        wm:         WorldModel,
    ) -> List[str]:
        """
        Generate human-readable causal insights from the analysis.

        Answers the four core causal questions:
        1. Which genes actually CAUSE wins?
        2. Which are just spurious correlations?
        3. Which genes survive regime changes?
        4. What does the world model recommend?
        """
        insights: List[str] = []

        causal_all    = [c for c in scorecards.values() if c.is_causal]
        causal_robust = [c for c in causal_all if not c.is_regime_sensitive]
        causal_sens   = [c for c in causal_all if c.is_regime_sensitive]
        spurious  = [c for c in scorecards.values() if c.is_spurious]
        robust    = sorted(
            scorecards.values(), key=lambda c: c.regime_robustness, reverse=True
        )
        fragile   = [c for c in scorecards.values() if c.regime_robustness < 0.05]

        # Q1: Causal genes (universal)
        if causal_robust:
            names = [c.gene_name for c in causal_robust]
            insights.append(
                f"CAUSAL genes (intervention-proven + regime-robust): {names}"
            )
            for c in sorted(causal_robust, key=lambda x: x.causal_score, reverse=True)[:3]:
                insights.append(
                    f"  → {c.gene_name}: causal_score={c.causal_score:.3f} "
                    f"partial_r={c.partial_corr:.3f} "
                    f"counterfactual_best={c.counterfactual_value}"
                )

        # Q1b: Causal but regime-sensitive
        if causal_sens:
            names = [c.gene_name for c in causal_sens]
            insights.append(
                f"CAUSAL_SENSITIVE genes (proven by intervention, but regime-specific): {names} — "
                f"these genes cause wins in some regimes but not others."
            )
            for c in sorted(causal_sens, key=lambda x: x.causal_score, reverse=True)[:3]:
                per_r = ", ".join(f"{r}={abs(v):.2f}" for r, v in c.per_regime_corr.items())
                insights.append(
                    f"  → {c.gene_name}: causal_score={c.causal_score:.3f} "
                    f"robust={c.regime_robustness:.3f} [{per_r}]"
                    f"  counterfactual={c.counterfactual_value}"
                )

        if not causal_all:
            insights.append(
                "No strongly CAUSAL genes found at current thresholds — "
                "consider increasing n_samples or intervention_m."
            )

        # Q2: Spurious correlations
        if spurious:
            names = [c.gene_name for c in spurious]
            insights.append(
                f"SPURIOUS genes (regime-specific correlation, NOT causal by intervention): {names}"
            )
            for c in spurious[:2]:
                worst_regime = min(c.per_regime_corr, key=lambda k: abs(c.per_regime_corr[k]))
                insights.append(
                    f"  ✗ {c.gene_name}: spurious_score={c.spurious_score:.3f} "
                    f"(weakest in regime {worst_regime}: r={c.per_regime_corr.get(worst_regime, 0):.3f})"
                )

        # Q3: Regime survivors
        if robust:
            top_robust = [c for c in robust if c.regime_robustness > 0.05][:3]
            if top_robust:
                insights.append(
                    "REGIME-ROBUST genes (survive across BULL/BEAR/SIDEWAYS): "
                    + str([c.gene_name for c in top_robust])
                )
                for c in top_robust:
                    per_r_str = ", ".join(
                        f"{r}={abs(v):.2f}" for r, v in c.per_regime_corr.items()
                    )
                    insights.append(f"  ✓ {c.gene_name}: {per_r_str}")

        if fragile:
            names = [c.gene_name for c in fragile[:5]]
            insights.append(
                f"FRAGILE genes (near-zero in at least one regime): {names} — "
                f"these lose predictive power when market regime shifts."
            )

        # Q4: World model recommendations
        r2_info = {k: round(v, 3) for k, v in wm.r2_scores.items()}
        insights.append(
            f"WorldModel R²: {r2_info} (global={wm.global_r2:.3f}) — "
            + ("model is reliable for counterfactual inference."
               if wm.global_r2 > 0.30
               else "model has limited fit; counterfactual values are approximate.")
        )

        # Counterfactual recommendations — prefer causal genes, then robust
        cf_recs = sorted(causal_all, key=lambda c: c.causal_score, reverse=True)[:5]
        if not cf_recs:
            cf_recs = sorted(
                scorecards.values(), key=lambda c: c.regime_robustness, reverse=True
            )[:5]
        if cf_recs:
            insights.append("Counterfactual recommendations (world-model optimal values):")
            for c in cf_recs:
                insights.append(
                    f"  {c.gene_name}: {c.counterfactual_value} "
                    f"(causal={c.is_causal}, robust={c.regime_robustness:.2f})"
                )

        if not insights:
            insights.append(
                "Analysis completed but no strong causal signals found. "
                "Increase n_samples for better coverage."
            )

        return insights
