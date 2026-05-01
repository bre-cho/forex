"""
SELF-EVOLVING AUTONOMOUS ENTERPRISE — Layer 8 of the Intelligence Stack.

Mục tiêu
--------
Đây là tầng cuối cùng và cao nhất: hệ thống không còn là một "system" nữa
mà là một **thực thể vận hành độc lập**:

    tự sinh chiến lược     — orchestrates all 7 layers to generate strategies
    tự phân bổ tài nguyên  — uses Sovereign Oversight directives (FULL_AUTO)
    tự vận hành            — runs autonomously in a background cycle loop
    tự tối ưu              — monitors its own performance every cycle
    tự tiến hóa qua thời gian — evolves its own governance policy

Architecture
------------
  AutonomousEnterpriseEngine
    ├─ EnterpriseConfig        — cycle timing, layer staleness, evolution params
    ├─ EnterpriseMemory        — rolling cycle history + champion policy tracking
    ├─ PolicyEvolver           — self-evolves SovereignPolicy from dominance trends
    │
    └─ run(app_state) → EnterpriseCycle
         Phase 1: Layer Orchestration
           └─ decide which layers are stale → run them in order:
              evolution → meta → causal → utility → ecosystem
         Phase 2: Sovereign Oversight (FULL_AUTO)
           └─ governs all clusters, applies directives to live system
         Phase 3: Performance Observation
           └─ read network_dominance → update memory
         Phase 4: Self-Evolution
           └─ PolicyEvolver.evolve() → mutate sovereign policy if needed

Layer Staleness
---------------
  Each layer has a `*_cycle_interval` (enterprise cycles between re-runs).
  This prevents running expensive engines (evolution, meta) every cycle.
  Cheap engines (utility, sovereign) run every cycle.

  Default intervals:
    evolution  : every 3 cycles  (expensive — genetic simulation)
    meta       : every 2 cycles  (medium — multiple evolution loops)
    causal     : every 2 cycles  (medium — world model + intervention)
    utility    : every 1 cycle   (fast — LP optimisation)
    ecosystem  : every 2 cycles  (medium — Nash IBR simulation)

Self-Evolution
--------------
  PolicyEvolver tracks raw_dominance across cycles.

  IMPROVING trend (3+ consecutive cycles):
    → tighten boost_threshold by 5%  (reward winners faster)
    → ease kill_threshold by 5%      (be more patient with bad clusters)

  DECLINING trend (3+ consecutive cycles):
    → ease boost_threshold by 10%   (lower bar for scale-up)
    → tighten kill_threshold by 10% (kill bad clusters faster)

  Champion Policy:
    → stored whenever a new peak dominance is achieved
    → reverted to if dominance drops > revert_threshold below champion peak

  Cycle Interval Adaptation:
    → SURVIVAL  : shrink interval to min_cycle_interval_secs
    → DOMINANCE : expand interval to max_cycle_interval_secs
    → Otherwise : use base cycle_interval_secs

Enterprise Lifecycle
--------------------
  IDLE    : engine created but not started
  RUNNING : background cycle loop is active
  STOPPED : loop was explicitly stopped

API endpoints (wired in main.py)
---------------------------------
  POST /api/enterprise/start   → start autonomous background loop
  POST /api/enterprise/stop    → gracefully stop the loop
  GET  /api/enterprise/status  → current lifecycle + recent cycle history
  POST /api/enterprise/evolve  → force one enterprise cycle synchronously
  GET  /api/enterprise/manifest → entity's self-model (consciousness snapshot)
"""

from __future__ import annotations

import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_EPS = 1e-9

# Staleness sentinel: any value ≥ this guarantees a layer runs on the first cycle.
_LAYER_STALE_INITIAL = 999

# Dominance delta thresholds for trend direction classification.
# Deltas larger than _TREND_THRESHOLD_UP are IMPROVING;
# more negative than _TREND_THRESHOLD_DOWN are DECLINING.
_TREND_THRESHOLD_UP:   float = 0.005
_TREND_THRESHOLD_DOWN: float = -0.005


# ── Enums ──────────────────────────────────────────────────────────────────── #

class EnterpriseLifecycle(str, Enum):
    IDLE    = "IDLE"     # not yet started
    RUNNING = "RUNNING"  # background loop active
    STOPPED = "STOPPED"  # explicitly stopped


class EnterpriseObjective(str, Enum):
    """
    The enterprise's self-assessed mission tier — derived from the
    sovereign objective_tree of the last cycle.
    """
    SURVIVAL   = "SURVIVAL"
    STABILITY  = "STABILITY"
    GROWTH     = "GROWTH"
    DOMINANCE  = "DOMINANCE"


# ── EnterpriseConfig ──────────────────────────────────────────────────────── #

@dataclass
class EnterpriseConfig:
    """
    Configuration for the Autonomous Enterprise Engine.

    Timing
    ------
    cycle_interval_secs     : seconds between autonomous cycles (default 900 = 15 min)
    min_cycle_interval_secs : floor — e.g. in SURVIVAL mode (default 60)
    max_cycle_interval_secs : ceiling — e.g. in DOMINANCE mode (default 3600)

    Layer staleness (re-run layer every N enterprise cycles)
    ---------------------------------------------------------
    evolution_cycle_interval : default 3
    meta_cycle_interval      : default 2
    causal_cycle_interval    : default 2
    utility_cycle_interval   : default 1 (runs every cycle)
    ecosystem_cycle_interval : default 2

    Layer execution params (fast configs for enterprise batch mode)
    ---------------------------------------------------------------
    ev_pop_size, ev_generations, ev_episodes, ev_bars
    meta_outer_loops, meta_pop_size, meta_generations, meta_episodes, meta_bars
    causal_n_samples, causal_episodes, causal_bars, causal_intervention_m
    eco_n_opponents, eco_n_candidates, eco_episodes, eco_bars, eco_nash_iter

    Self-evolution
    --------------
    auto_evolve              : enable PolicyEvolver (default True)
    evolution_window         : look-back cycles for trend detection (default 5)
    champion_revert_threshold: revert if dominance drops this fraction below peak
                               (default 0.20 = 20%)
    trend_consecutive_min    : cycles in same direction to trigger adaptation (default 3)
    policy_mutation_rate     : fraction of threshold to shift per adaptation (default 0.05)
    """
    # Timing
    cycle_interval_secs:     float = 900.0
    min_cycle_interval_secs: float = 60.0
    max_cycle_interval_secs: float = 3600.0

    # Layer staleness
    evolution_cycle_interval: int = 3
    meta_cycle_interval:      int = 2
    causal_cycle_interval:    int = 2
    utility_cycle_interval:   int = 1
    ecosystem_cycle_interval: int = 2

    # Evolution layer params
    ev_pop_size:      int = 15
    ev_generations:   int = 3
    ev_episodes:      int = 8
    ev_bars:          int = 60

    # Meta-learning params
    meta_outer_loops: int = 2
    meta_pop_size:    int = 15
    meta_generations: int = 3
    meta_episodes:    int = 8
    meta_bars:        int = 60
    meta_top_k:       int = 5

    # Causal params
    causal_n_samples:      int = 20
    causal_episodes:       int = 6
    causal_bars:           int = 60
    causal_intervention_m: int = 6

    # Ecosystem (game theory) params
    eco_n_opponents:   int = 4
    eco_n_candidates:  int = 10
    eco_episodes:      int = 8
    eco_bars:          int = 60
    eco_nash_iter:     int = 6

    # Self-evolution
    auto_evolve:               bool  = True
    evolution_window:          int   = 5
    champion_revert_threshold: float = 0.20
    trend_consecutive_min:     int   = 3
    policy_mutation_rate:      float = 0.05

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cycle_interval_secs":      self.cycle_interval_secs,
            "min_cycle_interval_secs":  self.min_cycle_interval_secs,
            "max_cycle_interval_secs":  self.max_cycle_interval_secs,
            "layer_staleness": {
                "evolution":  self.evolution_cycle_interval,
                "meta":       self.meta_cycle_interval,
                "causal":     self.causal_cycle_interval,
                "utility":    self.utility_cycle_interval,
                "ecosystem":  self.ecosystem_cycle_interval,
            },
            "auto_evolve":               self.auto_evolve,
            "evolution_window":          self.evolution_window,
            "champion_revert_threshold": self.champion_revert_threshold,
            "trend_consecutive_min":     self.trend_consecutive_min,
            "policy_mutation_rate":      self.policy_mutation_rate,
        }


# ── LayerRunRecord ────────────────────────────────────────────────────────── #

@dataclass
class LayerRunRecord:
    """
    Records the outcome of one layer execution within an enterprise cycle.

    Fields
    ------
    layer_id     : name of the layer (evolution/meta/causal/utility/ecosystem)
    ran          : True if the layer was actually executed this cycle
    skipped      : True if skipped due to staleness policy (not yet due)
    duration_secs: wall-clock time for this layer's run (0 if skipped)
    success      : True if run completed without exception
    error        : exception message if success=False
    summary      : Dict of key metrics from this layer's result
    """
    layer_id:     str
    ran:          bool
    skipped:      bool
    duration_secs: float
    success:      bool
    error:        Optional[str]
    summary:      Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "layer_id":      self.layer_id,
            "ran":           self.ran,
            "skipped":       self.skipped,
            "duration_secs": round(self.duration_secs, 3),
            "success":       self.success,
            "error":         self.error,
            "summary":       self.summary,
        }


# ── EnterpriseCycle ───────────────────────────────────────────────────────── #

@dataclass
class EnterpriseCycle:
    """
    Complete record of one autonomous enterprise cycle.

    Fields
    ------
    cycle_id          : unique identifier
    enterprise_cycle_n: sequential enterprise cycle number (1-based)
    layer_records     : List[LayerRunRecord] for each layer
    sovereign_cycle_id: cycle_id from SovereignOversightEngine
    raw_dominance     : network dominance achieved this cycle
    objective_level   : sovereign objective level active this cycle
    policy_evolved    : True if PolicyEvolver mutated the policy this cycle
    policy_mutation   : human-readable description of mutation (or None)
    reverted_to_champion: True if policy was reverted to champion
    insights          : List[str] enterprise-level analysis
    duration_secs     : total wall-clock time for this cycle
    completed_at      : unix timestamp
    """
    cycle_id:              str
    enterprise_cycle_n:    int
    layer_records:         List[LayerRunRecord]
    sovereign_cycle_id:    Optional[str]
    raw_dominance:         float
    objective_level:       str
    policy_evolved:        bool
    policy_mutation:       Optional[str]
    reverted_to_champion:  bool
    insights:              List[str]
    duration_secs:         float
    completed_at:          float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cycle_id":             self.cycle_id,
            "enterprise_cycle_n":   self.enterprise_cycle_n,
            "layer_records":        [r.to_dict() for r in self.layer_records],
            "sovereign_cycle_id":   self.sovereign_cycle_id,
            "raw_dominance":        round(self.raw_dominance, 4),
            "objective_level":      self.objective_level,
            "policy_evolved":       self.policy_evolved,
            "policy_mutation":      self.policy_mutation,
            "reverted_to_champion": self.reverted_to_champion,
            "insights":             self.insights,
            "duration_secs":        round(self.duration_secs, 3),
            "completed_at":         self.completed_at,
        }


# ── EnterpriseMemory ──────────────────────────────────────────────────────── #

@dataclass
class ChampionRecord:
    """Best policy configuration ever observed."""
    cycle_id:         str
    cycle_n:          int
    raw_dominance:    float
    policy_snapshot:  Dict[str, Any]  # serialised SovereignPolicy params
    achieved_at:      float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cycle_id":        self.cycle_id,
            "cycle_n":         self.cycle_n,
            "raw_dominance":   round(self.raw_dominance, 4),
            "policy_snapshot": self.policy_snapshot,
            "achieved_at":     self.achieved_at,
        }


class EnterpriseMemory:
    """
    Rolling history of enterprise cycles for self-learning.

    Tracks:
    - Last N raw_dominance values for trend detection
    - Champion policy (peak dominance configuration)
    - Consecutive trend direction counter
    """

    def __init__(self, window: int = 10) -> None:
        self._window         = window
        self._cycles:         List[EnterpriseCycle] = []
        self._dominance_hist: List[float]           = []
        self._champion:       Optional[ChampionRecord] = None
        self._trend_direction: str  = "STABLE"  # IMPROVING | STABLE | DECLINING
        self._consecutive:    int   = 0

    # ── cycle recording ── #

    def record(self, cycle: EnterpriseCycle) -> None:
        self._cycles.append(cycle)
        if len(self._cycles) > self._window * 2:
            self._cycles = self._cycles[-(self._window * 2):]

        self._dominance_hist.append(cycle.raw_dominance)
        if len(self._dominance_hist) > self._window:
            self._dominance_hist = self._dominance_hist[-self._window:]

        # Update champion
        if (
            self._champion is None
            or cycle.raw_dominance > self._champion.raw_dominance
        ):
            self._champion = ChampionRecord(
                cycle_id=cycle.cycle_id,
                cycle_n=cycle.enterprise_cycle_n,
                raw_dominance=cycle.raw_dominance,
                policy_snapshot={},  # caller fills this
                achieved_at=cycle.completed_at,
            )

        # Update trend direction
        if len(self._dominance_hist) >= 2:
            delta = self._dominance_hist[-1] - self._dominance_hist[-2]
            direction = ("IMPROVING" if delta > _TREND_THRESHOLD_UP
                         else "DECLINING" if delta < _TREND_THRESHOLD_DOWN
                         else "STABLE")
            if direction == self._trend_direction:
                self._consecutive += 1
            else:
                self._trend_direction = direction
                self._consecutive = 1

    def update_champion_policy(self, policy_snapshot: Dict[str, Any]) -> None:
        if self._champion is not None:
            self._champion.policy_snapshot = dict(policy_snapshot)

    # ── accessors ── #

    @property
    def champion(self) -> Optional[ChampionRecord]:
        return self._champion

    @property
    def trend_direction(self) -> str:
        return self._trend_direction

    @property
    def consecutive_trend_cycles(self) -> int:
        return self._consecutive

    @property
    def rolling_avg_dominance(self) -> float:
        if not self._dominance_hist:
            return 0.0
        return sum(self._dominance_hist) / len(self._dominance_hist)

    @property
    def recent_cycles(self) -> List[EnterpriseCycle]:
        return list(self._cycles[-10:])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trend_direction":        self._trend_direction,
            "consecutive_cycles":     self._consecutive,
            "rolling_avg_dominance":  round(self.rolling_avg_dominance, 4),
            "dominance_history":      [round(d, 4) for d in self._dominance_hist],
            "champion":               self._champion.to_dict() if self._champion else None,
            "total_cycles_recorded":  len(self._cycles),
        }


# ── PolicyEvolver ─────────────────────────────────────────────────────────── #

class PolicyEvolver:
    """
    Self-evolves the SovereignPolicy based on observed dominance trajectory.

    Algorithm
    ---------
    1. Read EnterpriseMemory.trend_direction and consecutive_trend_cycles.
    2. If IMPROVING for >= trend_consecutive_min cycles:
         → tighten boost_threshold (require higher sv to scale up)
         → ease kill_threshold (be more patient)
    3. If DECLINING for >= trend_consecutive_min cycles:
         → ease boost_threshold (lower bar for scale-up)
         → tighten kill_threshold (kill bad clusters faster)
    4. Champion revert: if current dominance < champion_peak × (1 - threshold):
         → restore saved champion policy params

    Returns (mutated_policy, description_str) or (unchanged_policy, None).
    """

    def evolve(
        self,
        policy: Any,  # SovereignPolicy
        memory: EnterpriseMemory,
        current_dominance: float,
        config: EnterpriseConfig,
    ) -> tuple:
        """
        Attempt to evolve the policy.

        Returns
        -------
        (new_policy, mutation_description, reverted)
        """
        # Import here to avoid circular at module load
        from engine.sovereign_oversight_engine import SovereignPolicy

        champion = memory.champion
        trend    = memory.trend_direction
        consec   = memory.consecutive_trend_cycles
        rate     = config.policy_mutation_rate

        # 1. Champion revert check
        if champion is not None and champion.raw_dominance > _EPS:
            drop_fraction = (champion.raw_dominance - current_dominance) / champion.raw_dominance
            if drop_fraction > config.champion_revert_threshold and champion.policy_snapshot:
                snap = champion.policy_snapshot
                reverted = _policy_from_snapshot(snap, policy)
                desc = (
                    f"🔄 Reverted to champion policy (cycle #{champion.cycle_n}, "
                    f"dominance={champion.raw_dominance:.4f}). "
                    f"Current dominance {current_dominance:.4f} dropped "
                    f"{drop_fraction*100:.1f}% below champion peak."
                )
                logger.info("PolicyEvolver: %s", desc)
                return reverted, desc, True

        # 2. Trend adaptation
        if consec < config.trend_consecutive_min:
            return policy, None, False

        new_boost = policy.boost_threshold
        new_kill  = policy.kill_threshold
        desc_parts: List[str] = []

        if trend == "IMPROVING":
            # Tighten requirements — we're doing well, be more selective
            new_boost = min(0.90, policy.boost_threshold + rate)
            new_kill  = max(0.05, policy.kill_threshold  - rate)
            desc_parts.append(
                f"📈 IMPROVING ×{consec}: tighten boost_threshold "
                f"{policy.boost_threshold:.3f}→{new_boost:.3f}, "
                f"ease kill_threshold "
                f"{policy.kill_threshold:.3f}→{new_kill:.3f}"
            )
        elif trend == "DECLINING":
            # Ease requirements — struggling, lower the bar to scale up, kill faster
            new_boost = max(0.50, policy.boost_threshold - rate * 2)
            new_kill  = min(0.35, policy.kill_threshold  + rate * 2)
            desc_parts.append(
                f"📉 DECLINING ×{consec}: ease boost_threshold "
                f"{policy.boost_threshold:.3f}→{new_boost:.3f}, "
                f"tighten kill_threshold "
                f"{policy.kill_threshold:.3f}→{new_kill:.3f}"
            )
        else:
            return policy, None, False

        if abs(new_boost - policy.boost_threshold) < _EPS and abs(new_kill - policy.kill_threshold) < _EPS:
            return policy, None, False

        evolved = SovereignPolicy(
            mode                      = policy.mode,
            objective_level           = policy.objective_level,
            max_lot_override          = policy.max_lot_override,
            min_lot_override          = policy.min_lot_override,
            kill_threshold            = round(new_kill, 4),
            throttle_threshold        = round(
                min(new_boost - 0.05, max(new_kill + 0.05, policy.throttle_threshold)), 4
            ),
            boost_threshold           = round(new_boost, 4),
            attention_normalize       = policy.attention_normalize,
            max_attention_per_cluster = policy.max_attention_per_cluster,
        )
        desc = "; ".join(desc_parts)
        logger.info("PolicyEvolver.evolve: %s", desc)
        return evolved, desc, False


def _policy_from_snapshot(snap: Dict[str, Any], fallback: Any) -> Any:
    """Reconstruct a SovereignPolicy from a saved snapshot dict."""
    from engine.sovereign_oversight_engine import SovereignPolicy, SovereignMode, ObjectiveLevel
    try:
        return SovereignPolicy(
            mode                      = SovereignMode(snap.get("mode", fallback.mode.value)),
            objective_level           = ObjectiveLevel(snap.get("objective_level", fallback.objective_level.value)),
            max_lot_override          = float(snap.get("max_lot_override",          fallback.max_lot_override)),
            min_lot_override          = float(snap.get("min_lot_override",          fallback.min_lot_override)),
            kill_threshold            = float(snap.get("kill_threshold",            fallback.kill_threshold)),
            throttle_threshold        = float(snap.get("throttle_threshold",        fallback.throttle_threshold)),
            boost_threshold           = float(snap.get("boost_threshold",           fallback.boost_threshold)),
            attention_normalize       = bool(snap.get("attention_normalize",        fallback.attention_normalize)),
            max_attention_per_cluster = float(snap.get("max_attention_per_cluster", fallback.max_attention_per_cluster)),
        )
    except Exception:
        return fallback


# ── AutonomousEnterpriseEngine ─────────────────────────────────────────────── #

class AutonomousEnterpriseEngine:
    """
    SELF-EVOLVING AUTONOMOUS ENTERPRISE — Layer 8.

    Orchestrates the full 7-layer intelligence stack autonomously,
    evolves its own governance policy, and operates as an independent entity.

    Usage
    -----
    engine = AutonomousEnterpriseEngine(config=EnterpriseConfig())

    # Synchronous single cycle:
    cycle = engine.run(app_state)

    # Background loop (managed by main.py asyncio task):
    # POST /api/enterprise/start   → launches asyncio background task
    # POST /api/enterprise/stop    → signals the task to stop
    # GET  /api/enterprise/status  → current state + history
    """

    def __init__(self, config: Optional[EnterpriseConfig] = None) -> None:
        from trading_core.engines._advanced_guard import require_advanced_engines
        require_advanced_engines("AutonomousEnterpriseEngine")
        from engine.sovereign_oversight_engine import SovereignPolicy, SovereignMode

        self.config:    EnterpriseConfig = config or EnterpriseConfig()
        self.lifecycle: EnterpriseLifecycle = EnterpriseLifecycle.IDLE
        self.memory:    EnterpriseMemory    = EnterpriseMemory(
            window=max(10, self.config.evolution_window * 2)
        )
        self._evolver:  PolicyEvolver       = PolicyEvolver()

        # Sovereign policy managed by the enterprise (starts in FULL_AUTO)
        self._policy = SovereignPolicy(
            mode=SovereignMode.FULL_AUTO,
        )

        # Sovereign engine owned by enterprise (separate from app_state.sovereign_engine)
        from engine.sovereign_oversight_engine import SovereignOversightEngine
        self._sovereign = SovereignOversightEngine(policy=self._policy)

        self._cycle_n:    int            = 0
        self._last_cycle: Optional[EnterpriseCycle] = None

        # Layer staleness counters: how many enterprise cycles since last run
        self._layer_since: Dict[str, int] = {
            "evolution":  _LAYER_STALE_INITIAL,  # start stale → run on first cycle
            "meta":       _LAYER_STALE_INITIAL,
            "causal":     _LAYER_STALE_INITIAL,
            "utility":    _LAYER_STALE_INITIAL,
            "ecosystem":  _LAYER_STALE_INITIAL,
        }

        # Background stop flag (set by stop())
        self._stop_requested: bool = False

    # ── Public interface ── #

    def start(self) -> None:
        """Mark the enterprise as running (loop started externally)."""
        self._stop_requested = False
        self.lifecycle = EnterpriseLifecycle.RUNNING
        logger.info("AutonomousEnterpriseEngine: RUNNING")

    def stop(self) -> None:
        """Signal the background loop to stop after the current cycle."""
        self._stop_requested = True
        self.lifecycle = EnterpriseLifecycle.STOPPED
        logger.info("AutonomousEnterpriseEngine: STOP requested")

    def is_stop_requested(self) -> bool:
        return self._stop_requested

    def current_cycle_interval(self) -> float:
        """
        Adaptive cycle interval based on current objective level.

        SURVIVAL  → min_cycle_interval (check frequently)
        DOMINANCE → max_cycle_interval (no need to rush)
        Others    → base cycle_interval
        """
        if self._last_cycle is None:
            return self.config.cycle_interval_secs
        obj = self._last_cycle.objective_level
        if obj == "SURVIVAL":
            return self.config.min_cycle_interval_secs
        if obj == "DOMINANCE":
            return self.config.max_cycle_interval_secs
        return self.config.cycle_interval_secs

    def run(self, app_state: Any) -> EnterpriseCycle:
        """
        Execute one autonomous enterprise cycle.

        Pipeline
        --------
        Phase 1 — Layer Orchestration
          → Run each stale layer (evolution/meta/causal/utility/ecosystem)
          → Apply each layer result to live system if successful
        Phase 2 — Sovereign Oversight (FULL_AUTO)
          → Govern the cluster portfolio
          → Apply directives to live system
        Phase 3 — Performance Observation
          → Read network_dominance from sovereign result
          → Update EnterpriseMemory
        Phase 4 — Self-Evolution (if auto_evolve=True)
          → PolicyEvolver.evolve() → update self._policy
          → Revert to champion if dominance dropped too far

        Returns EnterpriseCycle record.
        """
        t0 = time.time()
        self._cycle_n += 1
        cycle_id = f"ent-{int(t0)}-{self._cycle_n:04d}"
        logger.info("AutonomousEnterpriseEngine.run: cycle=%s n=%d", cycle_id, self._cycle_n)

        layer_records: List[LayerRunRecord] = []

        # ── Phase 1: Layer Orchestration ──────────────────────────── #

        self._run_evolution(app_state, layer_records)
        self._run_meta(app_state, layer_records)
        self._run_causal(app_state, layer_records)
        self._run_utility(app_state, layer_records)
        self._run_ecosystem(app_state, layer_records)

        # ── Phase 2: Sovereign Oversight ──────────────────────────── #

        sov_result = None
        sov_cycle_id = None
        try:
            self._sovereign.update_policy(self._policy)
            sov_result    = self._sovereign.run(app_state)
            sov_cycle_id  = sov_result.cycle_id
            app_state.sovereign_engine = self._sovereign
            app_state.sovereign_result = sov_result
            logger.info(
                "AutonomousEnterpriseEngine: sovereign done cycle=%s dominance=%.4f obj=%s",
                sov_cycle_id,
                sov_result.network_dominance.raw_dominance,
                sov_result.objective_tree.active_level.value,
            )
        except Exception as exc:
            logger.error("AutonomousEnterpriseEngine: sovereign failed — %s", exc)

        # ── Phase 3: Performance Observation ──────────────────────── #

        raw_dominance   = 0.0
        objective_level = "GROWTH"
        if sov_result is not None:
            raw_dominance   = sov_result.network_dominance.raw_dominance
            objective_level = sov_result.objective_tree.active_level.value

        # ── Phase 4: Self-Evolution ────────────────────────────────── #

        policy_evolved   = False
        policy_mutation  = None
        reverted         = False

        if self.config.auto_evolve and sov_result is not None:
            new_policy, mutation, reverted = self._evolver.evolve(
                policy=self._policy,
                memory=self.memory,
                current_dominance=raw_dominance,
                config=self.config,
            )
            if mutation is not None:
                self._policy    = new_policy
                policy_evolved  = True
                policy_mutation = mutation

        # ── Build cycle record ─────────────────────────────────────── #

        insights = self._build_insights(
            layer_records=layer_records,
            raw_dominance=raw_dominance,
            objective_level=objective_level,
            policy_evolved=policy_evolved,
            policy_mutation=policy_mutation,
            reverted=reverted,
            sov_result=sov_result,
        )

        duration = time.time() - t0
        cycle = EnterpriseCycle(
            cycle_id             = cycle_id,
            enterprise_cycle_n   = self._cycle_n,
            layer_records        = layer_records,
            sovereign_cycle_id   = sov_cycle_id,
            raw_dominance        = raw_dominance,
            objective_level      = objective_level,
            policy_evolved       = policy_evolved,
            policy_mutation      = policy_mutation,
            reverted_to_champion = reverted,
            insights             = insights,
            duration_secs        = duration,
        )
        self._last_cycle = cycle

        # Record in memory (before champion policy snapshot)
        self.memory.record(cycle)

        # Update champion policy snapshot after memory recorded it
        if (
            self.memory.champion is not None
            and self.memory.champion.cycle_id == cycle_id
        ):
            self.memory.update_champion_policy(self._policy.to_dict())

        logger.info(
            "AutonomousEnterpriseEngine.run: cycle=%s dominance=%.4f obj=%s "
            "evolved=%s dur=%.2fs",
            cycle_id, raw_dominance, objective_level, policy_evolved, duration,
        )
        return cycle

    # ── Layer execution helpers ─────────────────────────────────────── #

    def _is_stale(self, layer: str, interval: int) -> bool:
        return self._layer_since.get(layer, 999) >= interval

    def _mark_ran(self, layer: str) -> None:
        self._layer_since[layer] = 0

    def _mark_skipped(self, layer: str) -> None:
        self._layer_since[layer] = self._layer_since.get(layer, 0) + 1

    def _run_evolution(self, app_state: Any, records: List[LayerRunRecord]) -> None:
        cfg = self.config
        if not self._is_stale("evolution", cfg.evolution_cycle_interval):
            self._mark_skipped("evolution")
            records.append(LayerRunRecord(
                layer_id="evolution", ran=False, skipped=True,
                duration_secs=0.0, success=True, error=None,
                summary={"reason": "not stale"},
            ))
            return

        t0 = time.time()
        try:
            from engine.self_play_engine import EvolutionaryEngine
            engine = EvolutionaryEngine(
                pop_size=cfg.ev_pop_size,
                generations=cfg.ev_generations,
                episodes=cfg.ev_episodes,
                bars_per_episode=cfg.ev_bars,
            )
            result = engine.run()
            app_state.evolution_engine = engine
            app_state.evolution_result = result
            result.apply_to(app_state.decision_engine)
            self._mark_ran("evolution")
            pf = getattr(getattr(result, "best_fitness", None), "profit_factor", 0.0)
            records.append(LayerRunRecord(
                layer_id="evolution", ran=True, skipped=False,
                duration_secs=time.time() - t0,
                success=True, error=None,
                summary={"profit_factor": round(float(pf), 3)},
            ))
        except Exception as exc:
            self._mark_ran("evolution")
            logger.warning("Enterprise: evolution layer error — %s", exc)
            records.append(LayerRunRecord(
                layer_id="evolution", ran=True, skipped=False,
                duration_secs=time.time() - t0,
                success=False, error=str(exc), summary={},
            ))

    def _run_meta(self, app_state: Any, records: List[LayerRunRecord]) -> None:
        cfg = self.config
        if not self._is_stale("meta", cfg.meta_cycle_interval):
            self._mark_skipped("meta")
            records.append(LayerRunRecord(
                layer_id="meta", ran=False, skipped=True,
                duration_secs=0.0, success=True, error=None,
                summary={"reason": "not stale"},
            ))
            return

        t0 = time.time()
        try:
            from engine.meta_learning_engine import MetaLearningEngine
            engine = MetaLearningEngine(
                outer_loops=cfg.meta_outer_loops,
                pop_size=cfg.meta_pop_size,
                generations=cfg.meta_generations,
                episodes=cfg.meta_episodes,
                bars_per_episode=cfg.meta_bars,
                top_k_winners=cfg.meta_top_k,
            )
            result = engine.run()
            app_state.meta_engine = engine
            app_state.meta_result = result
            result.apply_to(app_state.decision_engine)
            self._mark_ran("meta")
            pf = getattr(getattr(result, "best_fitness", None), "profit_factor", 0.0)
            records.append(LayerRunRecord(
                layer_id="meta", ran=True, skipped=False,
                duration_secs=time.time() - t0,
                success=True, error=None,
                summary={"profit_factor": round(float(pf), 3)},
            ))
        except Exception as exc:
            self._mark_ran("meta")
            logger.warning("Enterprise: meta layer error — %s", exc)
            records.append(LayerRunRecord(
                layer_id="meta", ran=True, skipped=False,
                duration_secs=time.time() - t0,
                success=False, error=str(exc), summary={},
            ))

    def _run_causal(self, app_state: Any, records: List[LayerRunRecord]) -> None:
        cfg = self.config
        if not self._is_stale("causal", cfg.causal_cycle_interval):
            self._mark_skipped("causal")
            records.append(LayerRunRecord(
                layer_id="causal", ran=False, skipped=True,
                duration_secs=0.0, success=True, error=None,
                summary={"reason": "not stale"},
            ))
            return

        t0 = time.time()
        try:
            from engine.causal_strategy_engine import CausalStrategyEngine
            engine = CausalStrategyEngine(
                n_samples=cfg.causal_n_samples,
                episodes=cfg.causal_episodes,
                bars_per_episode=cfg.causal_bars,
                intervention_m=cfg.causal_intervention_m,
            )
            result = engine.run()
            app_state.causal_engine = engine
            app_state.causal_result = result
            result.apply_to(app_state.decision_engine)
            self._mark_ran("causal")
            sc_map = getattr(result, "causal_scorecards", {}) or {}
            avg_cs = (sum(getattr(sc, "causal_score", 0.0) for sc in sc_map.values())
                      / max(len(sc_map), 1))
            records.append(LayerRunRecord(
                layer_id="causal", ran=True, skipped=False,
                duration_secs=time.time() - t0,
                success=True, error=None,
                summary={"avg_causal_score": round(float(avg_cs), 3)},
            ))
        except Exception as exc:
            self._mark_ran("causal")
            logger.warning("Enterprise: causal layer error — %s", exc)
            records.append(LayerRunRecord(
                layer_id="causal", ran=True, skipped=False,
                duration_secs=time.time() - t0,
                success=False, error=str(exc), summary={},
            ))

    def _run_utility(self, app_state: Any, records: List[LayerRunRecord]) -> None:
        cfg = self.config
        if not self._is_stale("utility", cfg.utility_cycle_interval):
            self._mark_skipped("utility")
            records.append(LayerRunRecord(
                layer_id="utility", ran=False, skipped=True,
                duration_secs=0.0, success=True, error=None,
                summary={"reason": "not stale"},
            ))
            return

        t0 = time.time()
        try:
            from engine.utility_optimization_engine import UtilityOptimizationEngine
            engine = UtilityOptimizationEngine()
            result = engine.run(app_state)
            app_state.utility_engine = engine
            app_state.utility_result = result
            result.apply_to(app_state.decision_engine)
            self._mark_ran("utility")
            comp = getattr(getattr(result, "optimal_utility", None), "composite", 0.5)
            records.append(LayerRunRecord(
                layer_id="utility", ran=True, skipped=False,
                duration_secs=time.time() - t0,
                success=True, error=None,
                summary={"composite_utility": round(float(comp), 3)},
            ))
        except Exception as exc:
            self._mark_ran("utility")
            logger.warning("Enterprise: utility layer error — %s", exc)
            records.append(LayerRunRecord(
                layer_id="utility", ran=True, skipped=False,
                duration_secs=time.time() - t0,
                success=False, error=str(exc), summary={},
            ))

    def _run_ecosystem(self, app_state: Any, records: List[LayerRunRecord]) -> None:
        cfg = self.config
        if not self._is_stale("ecosystem", cfg.ecosystem_cycle_interval):
            self._mark_skipped("ecosystem")
            records.append(LayerRunRecord(
                layer_id="ecosystem", ran=False, skipped=True,
                duration_secs=0.0, success=True, error=None,
                summary={"reason": "not stale"},
            ))
            return

        t0 = time.time()
        try:
            from engine.game_theory_engine import GameTheoryEngine, EcosystemConfig
            eco_cfg = EcosystemConfig(
                n_opponents=cfg.eco_n_opponents,
                n_candidate_genomes=cfg.eco_n_candidates,
                episodes=cfg.eco_episodes,
                bars_per_episode=cfg.eco_bars,
                nash_iterations=cfg.eco_nash_iter,
            )
            engine = GameTheoryEngine(config=eco_cfg)
            result = engine.run()
            app_state.ecosystem_engine = engine
            app_state.ecosystem_result = result
            result.apply_to(app_state.decision_engine)
            self._mark_ran("ecosystem")
            records.append(LayerRunRecord(
                layer_id="ecosystem", ran=True, skipped=False,
                duration_secs=time.time() - t0,
                success=True, error=None,
                summary={
                    "ecosystem_pf": round(float(getattr(result, "ecosystem_pf", 0.0)), 3),
                    "exploitability": round(
                        sum(getattr(result, "exploitability", {}).values())
                        / max(len(getattr(result, "exploitability", {}) or {}), 1), 3
                    ),
                },
            ))
        except Exception as exc:
            self._mark_ran("ecosystem")
            logger.warning("Enterprise: ecosystem layer error — %s", exc)
            records.append(LayerRunRecord(
                layer_id="ecosystem", ran=True, skipped=False,
                duration_secs=time.time() - t0,
                success=False, error=str(exc), summary={},
            ))

    # ── Insights builder ────────────────────────────────────────────── #

    def _build_insights(
        self,
        layer_records: List[LayerRunRecord],
        raw_dominance: float,
        objective_level: str,
        policy_evolved: bool,
        policy_mutation: Optional[str],
        reverted: bool,
        sov_result: Any,
    ) -> List[str]:
        insights: List[str] = []

        obj_icon = {"SURVIVAL": "🆘", "STABILITY": "🛡️", "GROWTH": "📈", "DOMINANCE": "👑"}.get(
            objective_level, "❓"
        )
        insights.append(
            f"{obj_icon} Enterprise cycle #{self._cycle_n} — "
            f"objective={objective_level} dominance={raw_dominance:.4f}"
        )

        # Memory trend
        mem = self.memory
        traj_icon = {"IMPROVING": "🔺", "DECLINING": "🔻", "STABLE": "➡️"}.get(
            mem.trend_direction, "➡️"
        )
        insights.append(
            f"{traj_icon} Dominance trend: {mem.trend_direction} "
            f"(×{mem.consecutive_trend_cycles} cycles, "
            f"rolling avg={mem.rolling_avg_dominance:.4f})"
        )

        # Layers
        ran   = [r.layer_id for r in layer_records if r.ran and r.success]
        skips = [r.layer_id for r in layer_records if r.skipped]
        fails = [r.layer_id for r in layer_records if r.ran and not r.success]
        if ran:
            insights.append(f"⚙️  Layers run: {', '.join(ran)}")
        if skips:
            insights.append(f"⏭️  Layers skipped (not stale): {', '.join(skips)}")
        if fails:
            insights.append(f"❌ Layer failures: {', '.join(fails)}")

        # Policy evolution
        if reverted:
            insights.append(f"🔄 Policy reverted to champion: {policy_mutation}")
        elif policy_evolved and policy_mutation:
            insights.append(f"🧬 Policy self-evolved: {policy_mutation}")
        else:
            insights.append("🔒 Policy stable (no mutation this cycle)")

        # Champion
        champ = mem.champion
        if champ is not None:
            insights.append(
                f"🏆 Champion: cycle #{champ.cycle_n} "
                f"raw_dominance={champ.raw_dominance:.4f}"
            )

        # Sovereign summary
        if sov_result is not None:
            nd = sov_result.network_dominance
            insights.append(
                f"🌐 Network: raw={nd.raw_dominance:.4f} "
                f"risk-adj={nd.risk_adjusted_dominance:.4f} "
                f"efficiency={nd.portfolio_efficiency:.4f} "
                f"trajectory={nd.trajectory}"
            )

        return insights

    # ── Accessors ───────────────────────────────────────────────────── #

    @property
    def last_cycle(self) -> Optional[EnterpriseCycle]:
        return self._last_cycle

    @property
    def current_policy(self) -> Any:
        return self._policy

    @property
    def cycle_count(self) -> int:
        return self._cycle_n

    def manifest(self) -> Dict[str, Any]:
        """
        The enterprise's self-model — its current 'consciousness' snapshot.

        Returns a comprehensive view of:
        - What it is and what it's doing
        - Its current objective and policy
        - Its performance trajectory and champion
        - Its layer schedule and staleness state
        - Its evolution history in compact form
        """
        mem = self.memory

        return {
            "entity":        "SELF-EVOLVING AUTONOMOUS ENTERPRISE",
            "layer":         8,
            "description":   (
                "Thực thể vận hành độc lập — tự sinh chiến lược, tự phân bổ tài nguyên, "
                "tự vận hành, tự tối ưu, tự tiến hóa qua thời gian."
            ),
            "lifecycle":     self.lifecycle.value,
            "cycle_n":       self._cycle_n,
            "current_policy": self._policy.to_dict(),
            "memory":         mem.to_dict(),
            "layer_schedule": {
                layer: {
                    "interval":    interval,
                    "since_last":  self._layer_since.get(layer, 0),
                    "due":         self._is_stale(layer, interval),
                }
                for layer, interval in [
                    ("evolution",  self.config.evolution_cycle_interval),
                    ("meta",       self.config.meta_cycle_interval),
                    ("causal",     self.config.causal_cycle_interval),
                    ("utility",    self.config.utility_cycle_interval),
                    ("ecosystem",  self.config.ecosystem_cycle_interval),
                ]
            },
            "next_cycle_interval_secs": self.current_cycle_interval(),
            "config":                   self.config.to_dict(),
            "recent_cycles":            [c.to_dict() for c in mem.recent_cycles],
        }

    def status(self) -> Dict[str, Any]:
        """Compact status for /api/enterprise/status."""
        last = self._last_cycle
        mem  = self.memory
        return {
            "lifecycle":          self.lifecycle.value,
            "cycle_n":            self._cycle_n,
            "objective_level":    last.objective_level if last else "GROWTH",
            "raw_dominance":      last.raw_dominance if last else 0.0,
            "trend":              mem.trend_direction,
            "consecutive_trend":  mem.consecutive_trend_cycles,
            "rolling_avg":        round(mem.rolling_avg_dominance, 4),
            "champion": (
                {
                    "cycle_n":       mem.champion.cycle_n,
                    "raw_dominance": round(mem.champion.raw_dominance, 4),
                }
                if mem.champion else None
            ),
            "next_cycle_secs":    round(self.current_cycle_interval()),
            "last_cycle":         last.to_dict() if last else None,
        }
