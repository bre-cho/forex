"""
Strategic Sovereign Oversight Layer.

Mục tiêu
--------
Tầng tối cao (layer 7) của intelligence stack — đứng trên GameTheoryEngine
để quản trị toàn bộ ecosystem như một hệ điều hành:

  Mục tiêu không còn là "best strategy per engine" mà là "best outcome toàn hệ":
    • Đặt mục tiêu mạng-cấp (network-level objectives).
    • Phân bổ nguồn lực (compute + capital + action-rate) cho từng cluster.
    • Quyết định cluster nào được scale, throttle, suspend, kill.
    • Điều phối attention budget theo regime và risk state.
    • Chiến lược hóa toàn bộ ecosystem theo objective hierarchy.

Architecture
------------
  SovereignOversightEngine
    ├─ Phase 1: Telemetry Collection
    │    └─ ClusterTelemetry: đọc trạng thái từ mọi engine trong AppState
    │         EvolutionCluster, MetaCluster, CausalCluster,
    │         UtilityCluster, EcosystemCluster
    │
    ├─ Phase 2: Cluster Scoring
    │    └─ ClusterScorer: chấm điểm strategic value mỗi cluster
    │         strategic_value = f(roi_score, confidence, compute_efficiency,
    │                            risk_penalty, exploitability_exposure)
    │
    ├─ Phase 3: Resource Allocation
    │    └─ ResourceAllocator: phân bổ attention_budget dựa trên cluster scores
    │         + sovereign policy (objective hierarchy, risk state)
    │
    ├─ Phase 4: Governance Decisions
    │    └─ GovernanceDirector: ban hành lệnh per-cluster
    │         ClusterDirective: SCALE_UP | THROTTLE | SUSPEND | KILL | MAINTAIN
    │         với rationale, evidence_metrics, confidence
    │
    ├─ Phase 5: Policy Application (chỉ trong full_auto hoặc khi được gọi)
    │    └─ SovereignPolicyApplicator: áp dụng các directive vào live system
    │
    └─ SovereignOversightResult
         ├─ cluster_states     : Dict[cluster_id → ClusterState]
         ├─ directives         : Dict[cluster_id → ClusterDirective]
         ├─ resource_allocation: Dict[cluster_id → float]  (0..1)
         ├─ sovereign_policy   : SovereignPolicy
         ├─ governance_insights: List[str]
         ├─ audit_trail        : List[AuditEntry]
         └─ apply_to(app_state)

Cluster Lifecycle
-----------------
  ACTIVE    → cluster healthy, gets normal allocation
  THROTTLED → cluster underperforming, allocation halved
  SUSPENDED → cluster idle / no result yet, zero allocation
  KILLED    → cluster harmful, allocation zeroed + lot_scale capped

Sovereign Objective Hierarchy
------------------------------
  Level 1 — SURVIVAL   : hard stop if system drawdown critical
  Level 2 — STABILITY  : dampen volatility, reduce lot sizes
  Level 3 — GROWTH     : prioritise high-ROI clusters
  Level 4 — DOMINANCE  : reallocate to highest-value strategies

SovereignPolicy (all configurable at runtime)
---------------------------------------------
  mode                : ADVISORY | SEMI_AUTO | FULL_AUTO
  objective_level     : SURVIVAL | STABILITY | GROWTH | DOMINANCE
  max_lot_override    : hard cap on lot_scale (guardrail)
  min_lot_override    : floor on lot_scale
  kill_threshold      : cluster strategic_value below which KILL is issued
  throttle_threshold  : cluster strategic_value below which THROTTLE is issued
  boost_threshold     : cluster strategic_value above which SCALE_UP is issued
  attention_normalize : normalize all attention budgets to sum 1.0

Rollout Phases
--------------
  ADVISORY  : computes + logs directives only, does NOT apply to live
  SEMI_AUTO : applies MAINTAIN + THROTTLE; KILL/SCALE_UP require explicit /apply
  FULL_AUTO : applies ALL directives automatically on run()

API endpoints (wired in main.py)
---------------------------------
  POST /api/sovereign/run     → trigger oversight cycle
  GET  /api/sovereign/status  → SovereignOversightResult.to_dict()
  GET  /api/sovereign/policy  → current SovereignPolicy + objective tree
  POST /api/sovereign/apply   → apply last directives to live system
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_EPS = 1e-9

# ── UPGRADE directive thresholds ──────────────────────────────────────────── #

# A cluster is marked UPGRADE when it has demonstrated reliability (confidence
# above this value) but its actual ROI is lower than expected — meaning it is
# under-utilised and should be run with more aggressive parameters.
_UPGRADE_MIN_CONFIDENCE  = 0.60  # cluster must be at least this confident
_UPGRADE_MAX_ROI         = 0.40  # …but ROI is still below this value


# ── Enums ─────────────────────────────────────────────────────────────────── #

class SovereignMode(str, Enum):
    ADVISORY  = "ADVISORY"    # compute directives only, never apply
    SEMI_AUTO = "SEMI_AUTO"   # apply safe actions; require confirm for KILL/SCALE
    FULL_AUTO = "FULL_AUTO"   # apply all directives automatically


class ObjectiveLevel(str, Enum):
    SURVIVAL   = "SURVIVAL"   # drawdown critical — kill everything risky
    STABILITY  = "STABILITY"  # dampen — reduce lots, throttle aggressive clusters
    GROWTH     = "GROWTH"     # normal — reward high-ROI, penalise low-value
    DOMINANCE  = "DOMINANCE"  # offensive — scale winners, kill laggards fast


class ClusterLifecycle(str, Enum):
    ACTIVE    = "ACTIVE"
    THROTTLED = "THROTTLED"
    SUSPENDED = "SUSPENDED"
    KILLED    = "KILLED"


class DirectiveType(str, Enum):
    SCALE_UP  = "SCALE_UP"   # boost lot_scale, increase run frequency
    THROTTLE  = "THROTTLE"   # halve allocation, reduce lot_scale
    SUSPEND   = "SUSPEND"    # zero allocation, skip in next cycle
    KILL      = "KILL"       # zero allocation + hard lot_scale cap
    MAINTAIN  = "MAINTAIN"   # no change
    UPGRADE   = "UPGRADE"    # high-confidence but under-utilised — run with more aggressive params
    MERGE     = "MERGE"      # advisory only: cluster redundant, recommend merging into stronger peer


# ── SovereignPolicy ───────────────────────────────────────────────────────── #

@dataclass
class SovereignPolicy:
    """
    Configuration for the Sovereign Oversight Layer.

    Parameters
    ----------
    mode                : ADVISORY | SEMI_AUTO | FULL_AUTO
    objective_level     : current strategic objective tier
    max_lot_override    : hard cap on lot_scale applied to all clusters
    min_lot_override    : floor on lot_scale (0 = no floor)
    kill_threshold      : strategic_value ≤ this → KILL directive
    throttle_threshold  : strategic_value ≤ this → THROTTLE directive
    boost_threshold     : strategic_value ≥ this → SCALE_UP directive
    attention_normalize : normalize attention allocation to sum 1.0
    max_attention_per_cluster : cap attention share of any single cluster
    """
    mode:                     SovereignMode  = SovereignMode.ADVISORY
    objective_level:          ObjectiveLevel = ObjectiveLevel.GROWTH
    max_lot_override:         float          = 2.0
    min_lot_override:         float          = 0.0
    kill_threshold:           float          = 0.15
    throttle_threshold:       float          = 0.35
    boost_threshold:          float          = 0.70
    attention_normalize:      bool           = True
    max_attention_per_cluster: float         = 0.50  # no single cluster > 50 %

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode":                      self.mode.value,
            "objective_level":           self.objective_level.value,
            "max_lot_override":          round(self.max_lot_override, 3),
            "min_lot_override":          round(self.min_lot_override, 3),
            "kill_threshold":            round(self.kill_threshold, 3),
            "throttle_threshold":        round(self.throttle_threshold, 3),
            "boost_threshold":           round(self.boost_threshold, 3),
            "attention_normalize":       self.attention_normalize,
            "max_attention_per_cluster": round(self.max_attention_per_cluster, 3),
        }

    @classmethod
    def survival_policy(cls) -> "SovereignPolicy":
        """Emergency survival mode — maximum caution."""
        return cls(
            objective_level       = ObjectiveLevel.SURVIVAL,
            max_lot_override      = 0.25,
            kill_threshold        = 0.40,
            throttle_threshold    = 0.60,
            boost_threshold       = 0.90,
        )

    @classmethod
    def stability_policy(cls) -> "SovereignPolicy":
        """Conservative stability mode."""
        return cls(
            objective_level    = ObjectiveLevel.STABILITY,
            max_lot_override   = 0.75,
            kill_threshold     = 0.25,
            throttle_threshold = 0.45,
            boost_threshold    = 0.80,
        )

    @classmethod
    def dominance_policy(cls) -> "SovereignPolicy":
        """Aggressive dominance mode — reward winners hard."""
        return cls(
            objective_level    = ObjectiveLevel.DOMINANCE,
            max_lot_override   = 2.00,
            kill_threshold     = 0.20,
            throttle_threshold = 0.40,
            boost_threshold    = 0.65,
        )


# ── ClusterState ─────────────────────────────────────────────────────────── #

@dataclass
class ClusterState:
    """
    Telemetry snapshot for one engine cluster.

    Fields
    ------
    cluster_id      : unique name (evolution/meta/causal/utility/ecosystem)
    has_result      : whether the engine has a completed result
    lifecycle       : current lifecycle state
    strategic_value : composite score in [0, 1] (0=worthless, 1=perfect)
    roi_score       : profit-factor-based ROI normalised to [0, 1]
    confidence      : engine's self-reported confidence metric [0, 1]
    risk_penalty    : drawdown / exploitability penalty [0, 1]
    compute_score   : efficiency of compute spent (result quality / time) [0, 1]
    attention_budget: current allocation in [0, 1]
    lot_scale       : current genome lot_scale if applied
    last_run_secs   : wall-clock duration of last run (0 if not run)
    applied_to_live : True if engine result has been applied
    extra           : engine-specific extra fields
    """
    cluster_id:      str
    has_result:      bool
    lifecycle:       ClusterLifecycle
    strategic_value: float = 0.0
    roi_score:       float = 0.0
    confidence:      float = 0.0
    risk_penalty:    float = 0.0
    compute_score:   float = 0.0
    attention_budget: float = 0.0
    lot_scale:       float = 1.0
    last_run_secs:   float = 0.0
    applied_to_live: bool  = False
    extra:           Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cluster_id":      self.cluster_id,
            "has_result":      self.has_result,
            "lifecycle":       self.lifecycle.value,
            "strategic_value": round(self.strategic_value, 4),
            "roi_score":       round(self.roi_score, 4),
            "confidence":      round(self.confidence, 4),
            "risk_penalty":    round(self.risk_penalty, 4),
            "compute_score":   round(self.compute_score, 4),
            "attention_budget": round(self.attention_budget, 4),
            "lot_scale":       round(self.lot_scale, 3),
            "last_run_secs":   round(self.last_run_secs, 2),
            "applied_to_live": self.applied_to_live,
            "extra":           self.extra,
        }


# ── ClusterDirective ─────────────────────────────────────────────────────── #

@dataclass
class ClusterDirective:
    """
    A governance directive issued by the Sovereign for one cluster.

    Fields
    ------
    cluster_id       : target cluster
    directive        : SCALE_UP | THROTTLE | SUSPEND | KILL | MAINTAIN
    new_attention    : suggested new attention budget [0, 1]
    lot_scale_cap    : max lot_scale to enforce (None = no change)
    rationale        : human-readable explanation
    evidence_metrics : Dict of supporting metrics
    confidence       : confidence in this directive [0, 1]
    """
    cluster_id:       str
    directive:        DirectiveType
    new_attention:    float
    lot_scale_cap:    Optional[float]
    rationale:        str
    evidence_metrics: Dict[str, Any]
    confidence:       float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cluster_id":       self.cluster_id,
            "directive":        self.directive.value,
            "new_attention":    round(self.new_attention, 4),
            "lot_scale_cap":    round(self.lot_scale_cap, 3) if self.lot_scale_cap is not None else None,
            "rationale":        self.rationale,
            "evidence_metrics": {k: (round(v, 4) if isinstance(v, float) else v)
                                 for k, v in self.evidence_metrics.items()},
            "confidence":       round(self.confidence, 4),
        }


# ── AuditEntry ───────────────────────────────────────────────────────────── #

@dataclass
class AuditEntry:
    """
    Immutable audit record for one governance cycle.

    Every directive is recorded here for replay and post-analysis.
    """
    cycle_id:      str
    timestamp:     float
    cluster_id:    str
    directive:     str
    rationale:     str
    objective_level: str
    sovereign_mode:  str
    applied:         bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cycle_id":       self.cycle_id,
            "timestamp":      self.timestamp,
            "cluster_id":     self.cluster_id,
            "directive":      self.directive,
            "rationale":      self.rationale,
            "objective_level": self.objective_level,
            "sovereign_mode":  self.sovereign_mode,
            "applied":         self.applied,
        }


# ── NetworkObjectiveTree ──────────────────────────────────────────────────── #

@dataclass
class NetworkObjectiveTree:
    """
    Describes the current objective hierarchy and status at network level.

    Fields
    ------
    active_level         : current ObjectiveLevel
    survival_triggered   : True if drawdown crossed survival threshold
    stability_score      : system-wide stability measure [0, 1]
    growth_score         : system-wide growth momentum [0, 1]
    dominance_score      : strategic dominance index [0, 1]
    total_attention      : sum of all cluster attention budgets
    healthy_clusters     : number of ACTIVE clusters
    total_clusters       : total clusters managed
    """
    active_level:      ObjectiveLevel
    survival_triggered: bool
    stability_score:   float
    growth_score:      float
    dominance_score:   float
    total_attention:   float
    healthy_clusters:  int
    total_clusters:    int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "active_level":       self.active_level.value,
            "survival_triggered": self.survival_triggered,
            "stability_score":    round(self.stability_score, 4),
            "growth_score":       round(self.growth_score, 4),
            "dominance_score":    round(self.dominance_score, 4),
            "total_attention":    round(self.total_attention, 4),
            "healthy_clusters":   self.healthy_clusters,
            "total_clusters":     self.total_clusters,
        }


# ── NetworkDominanceScore ─────────────────────────────────────────────────── #

@dataclass
class NetworkDominanceScore:
    """
    Measures the total network dominance of the engine ecosystem.

    The goal is max total network dominance — not local per-cluster wins.
    Manages the cluster portfolio like an attention investment fund.

    Fields
    ------
    raw_dominance          : Σ(sv_i × α_i) — weighted strategic value
    risk_adjusted_dominance: raw × (1 − portfolio_risk) — penalty for aggregate risk
    portfolio_risk         : Σ(risk_penalty_i × α_i) — weighted portfolio risk
    portfolio_efficiency   : raw_dominance / max_possible (0=waste, 1=optimal)
    concentration_hhi      : Herfindahl index of attention dist (0=diverse, 1=concentrated)
    n_active_clusters      : number of ACTIVE clusters contributing
    delta_vs_previous      : change vs previous cycle (None if first cycle)
    trajectory             : "IMPROVING" | "STABLE" | "DECLINING"
    """
    raw_dominance:           float
    risk_adjusted_dominance: float
    portfolio_risk:          float
    portfolio_efficiency:    float
    concentration_hhi:       float
    n_active_clusters:       int
    delta_vs_previous:       Optional[float]
    trajectory:              str  # "IMPROVING" | "STABLE" | "DECLINING"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "raw_dominance":            round(self.raw_dominance, 4),
            "risk_adjusted_dominance":  round(self.risk_adjusted_dominance, 4),
            "portfolio_risk":           round(self.portfolio_risk, 4),
            "portfolio_efficiency":     round(self.portfolio_efficiency, 4),
            "concentration_hhi":        round(self.concentration_hhi, 4),
            "n_active_clusters":        self.n_active_clusters,
            "delta_vs_previous":        round(self.delta_vs_previous, 4) if self.delta_vs_previous is not None else None,
            "trajectory":               self.trajectory,
        }


# ── SovereignOversightResult ─────────────────────────────────────────────── #

@dataclass
class SovereignOversightResult:
    """
    Complete output of one SovereignOversightEngine.run() cycle.

    Fields
    ------
    cycle_id           : unique ID for this oversight cycle
    cluster_states     : Dict[cluster_id → ClusterState]
    directives         : Dict[cluster_id → ClusterDirective]
    resource_allocation: Dict[cluster_id → attention_budget]
    sovereign_policy   : SovereignPolicy used
    objective_tree     : NetworkObjectiveTree snapshot
    network_dominance  : NetworkDominanceScore — portfolio-level dominance metrics
    governance_insights: List[str] human-readable analysis
    audit_trail        : List[AuditEntry] (cumulative across cycles)
    duration_secs      : wall-clock time for this cycle
    completed_at       : unix timestamp
    applied_to_live    : True after apply_to()
    """
    cycle_id:            str
    cluster_states:      Dict[str, ClusterState]
    directives:          Dict[str, ClusterDirective]
    resource_allocation: Dict[str, float]
    sovereign_policy:    SovereignPolicy
    objective_tree:      NetworkObjectiveTree
    network_dominance:   NetworkDominanceScore
    governance_insights: List[str]
    audit_trail:         List[AuditEntry]
    duration_secs:       float
    completed_at:        float = field(default_factory=time.time)
    applied_to_live:     bool  = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cycle_id":             self.cycle_id,
            "cluster_states":       {k: v.to_dict() for k, v in self.cluster_states.items()},
            "directives":           {k: v.to_dict() for k, v in self.directives.items()},
            "resource_allocation":  {k: round(v, 4) for k, v in self.resource_allocation.items()},
            "sovereign_policy":     self.sovereign_policy.to_dict(),
            "objective_tree":       self.objective_tree.to_dict(),
            "network_dominance":    self.network_dominance.to_dict(),
            "governance_insights":  self.governance_insights,
            "audit_trail":          [a.to_dict() for a in self.audit_trail],
            "duration_secs":        round(self.duration_secs, 3),
            "completed_at":         self.completed_at,
            "applied_to_live":      self.applied_to_live,
        }

    def apply_to(self, app_state: Any) -> None:
        """
        Apply sovereign directives to the live system.

        For each cluster directive:
          - SCALE_UP  : increase lot_scale toward max_lot_override
          - THROTTLE  : halve lot_scale
          - KILL      : cap lot_scale to 0.25 (minimal viable)
          - SUSPEND   : no lot_scale change (cluster just not scheduled)
          - MAINTAIN  : no change

        Guardrails:
          - lot_scale is always bounded by [min_lot_override, max_lot_override]
          - RiskManager drawdown flags are never overridden
        """
        de = getattr(app_state, "decision_engine", None)
        if de is None:
            logger.warning("SovereignOversightResult.apply_to: no decision_engine on app_state")
            return

        ctrl = getattr(de, "controller", None)
        if ctrl is None:
            logger.warning("SovereignOversightResult.apply_to: no controller on decision_engine")
            return

        policy     = self.sovereign_policy
        current_lot = getattr(ctrl._state, "lot_scale", 1.0)

        # Aggregate lot_scale signal across directives
        lot_signals: List[Tuple[DirectiveType, Optional[float]]] = []
        for d in self.directives.values():
            lot_signals.append((d.directive, d.lot_scale_cap))

        # Determine effective lot_scale from directives
        new_lot = current_lot
        has_kill     = any(dt == DirectiveType.KILL     for dt, _ in lot_signals)
        has_throttle = any(dt == DirectiveType.THROTTLE for dt, _ in lot_signals)
        has_scale_up = any(dt == DirectiveType.SCALE_UP for dt, _ in lot_signals)

        if has_kill:
            # Hard cap: survival / killed cluster signals max conservatism
            new_lot = min(new_lot, 0.25)
        elif has_throttle and not has_scale_up:
            # Throttle: reduce by 30 %
            new_lot = new_lot * 0.70
        elif has_scale_up and not has_throttle:
            # Scale: increase by 20 %, capped by policy
            new_lot = new_lot * 1.20

        # Apply lot_scale_cap from explicit directives
        for dt, cap in lot_signals:
            if cap is not None:
                new_lot = min(new_lot, cap)

        # Enforce policy guardrails
        new_lot = max(policy.min_lot_override or 0.01, new_lot)
        new_lot = min(policy.max_lot_override, new_lot)
        new_lot = round(max(0.01, min(4.0, new_lot)), 3)

        ctrl._state.lot_scale = new_lot

        self.applied_to_live = True
        logger.info(
            "SovereignOversightResult.apply_to: cycle=%s lot_scale %.3f → %.3f "
            "objective=%s mode=%s",
            self.cycle_id,
            current_lot,
            new_lot,
            policy.objective_level.value,
            policy.mode.value,
        )


# ── Internal: Telemetry Collection ───────────────────────────────────────── #

_CLUSTER_IDS = ["evolution", "meta", "causal", "utility", "ecosystem"]


def _collect_telemetry(app_state: Any) -> Dict[str, ClusterState]:
    """
    Extract ClusterState for each engine from app_state.

    Reads the result objects already cached in app_state:
      evolution_result, meta_result, causal_result,
      utility_result, ecosystem_result
    """
    states: Dict[str, ClusterState] = {}

    # ── Evolution cluster ────────────────────────────────────────────── #
    ev = getattr(app_state, "evolution_result", None)
    if ev is not None:
        pf   = getattr(ev, "best_fitness", None)
        pf_v = float(getattr(pf, "profit_factor", 1.0)) if pf else 1.0
        dd   = float(getattr(pf, "max_drawdown",  0.0)) if pf else 0.0
        dur  = float(getattr(ev, "duration_secs", 0.0))
        applied = bool(getattr(ev, "applied_to_live", False))
        roi  = math.tanh(max(0.0, pf_v - 1.0))          # 0 → 0, 2 → 0.76
        conf = max(0.0, 1.0 - dd)
        cs   = 1.0 - max(0.0, min(1.0, dur / 300.0))    # longer = less efficient
        rp   = min(1.0, dd * 2.0)
        sv   = _strategic_value(roi, conf, cs, rp)
        states["evolution"] = ClusterState(
            cluster_id="evolution",
            has_result=True,
            lifecycle=ClusterLifecycle.ACTIVE,
            strategic_value=sv,
            roi_score=roi,
            confidence=conf,
            risk_penalty=rp,
            compute_score=cs,
            lot_scale=float(getattr(getattr(ev, "best_genome", None), "lot_scale", 1.0)
                            if hasattr(ev, "best_genome") else 1.0),
            last_run_secs=dur,
            applied_to_live=applied,
            extra={"profit_factor": round(pf_v, 3), "max_drawdown": round(dd, 3)},
        )
    else:
        states["evolution"] = ClusterState(
            cluster_id="evolution",
            has_result=False,
            lifecycle=ClusterLifecycle.SUSPENDED,
            extra={},
        )

    # ── Meta-Learning cluster ────────────────────────────────────────── #
    meta = getattr(app_state, "meta_result", None)
    if meta is not None:
        pf   = getattr(meta, "best_fitness", None)
        pf_v = float(getattr(pf, "profit_factor", 1.0)) if pf else 1.0
        dd   = float(getattr(pf, "max_drawdown",  0.0)) if pf else 0.0
        dur  = float(getattr(meta, "total_duration_secs", 0.0))
        applied = bool(getattr(meta, "applied_to_live", False))
        gi   = getattr(meta, "gene_importances", {}) or {}
        n_dominant = sum(1 for imp in gi.values()
                         if getattr(imp, "importance", 0.0) > 0.5)
        conf_bonus = min(0.2, n_dominant * 0.04)
        roi  = math.tanh(max(0.0, pf_v - 1.0))
        conf = min(1.0, max(0.0, 1.0 - dd) + conf_bonus)
        cs   = 1.0 - max(0.0, min(1.0, dur / 600.0))
        rp   = min(1.0, dd * 2.0)
        sv   = _strategic_value(roi, conf, cs, rp)
        states["meta"] = ClusterState(
            cluster_id="meta",
            has_result=True,
            lifecycle=ClusterLifecycle.ACTIVE,
            strategic_value=sv,
            roi_score=roi,
            confidence=conf,
            risk_penalty=rp,
            compute_score=cs,
            lot_scale=float(getattr(getattr(meta, "best_genome", None), "lot_scale", 1.0)
                            if hasattr(meta, "best_genome") else 1.0),
            last_run_secs=dur,
            applied_to_live=applied,
            extra={"profit_factor": round(pf_v, 3), "n_dominant_genes": n_dominant},
        )
    else:
        states["meta"] = ClusterState(
            cluster_id="meta",
            has_result=False,
            lifecycle=ClusterLifecycle.SUSPENDED,
            extra={},
        )

    # ── Causal cluster ───────────────────────────────────────────────── #
    causal = getattr(app_state, "causal_result", None)
    if causal is not None:
        sc_map = getattr(causal, "causal_scorecards", {}) or {}
        # Average causal_score across all genes as ROI proxy
        causal_scores = [getattr(sc, "causal_score", 0.0) for sc in sc_map.values()]
        spurious_avg  = sum(getattr(sc, "spurious_score", 0.0) for sc in sc_map.values()) / max(len(sc_map), 1)
        roi   = float(sum(causal_scores) / max(len(causal_scores), 1))
        conf  = 1.0 - spurious_avg
        dur   = float(getattr(causal, "duration_secs", 0.0))
        cs    = 1.0 - max(0.0, min(1.0, dur / 300.0))
        rp    = min(1.0, spurious_avg * 2.0)
        sv    = _strategic_value(roi, conf, cs, rp)
        applied = bool(getattr(causal, "applied_to_live", False))
        states["causal"] = ClusterState(
            cluster_id="causal",
            has_result=True,
            lifecycle=ClusterLifecycle.ACTIVE,
            strategic_value=sv,
            roi_score=roi,
            confidence=conf,
            risk_penalty=rp,
            compute_score=cs,
            lot_scale=float(getattr(getattr(causal, "counterfactual_genome", None), "lot_scale", 1.0)
                            if hasattr(causal, "counterfactual_genome") else 1.0),
            last_run_secs=dur,
            applied_to_live=applied,
            extra={"avg_causal_score": round(roi, 3), "spurious_avg": round(spurious_avg, 3)},
        )
    else:
        states["causal"] = ClusterState(
            cluster_id="causal",
            has_result=False,
            lifecycle=ClusterLifecycle.SUSPENDED,
            extra={},
        )

    # ── Utility cluster ──────────────────────────────────────────────── #
    util = getattr(app_state, "utility_result", None)
    if util is not None:
        uv    = getattr(util, "optimal_utility", None)
        comp  = float(getattr(uv, "composite",   0.5)) if uv else 0.5
        trust = float(getattr(uv, "trust_u",     0.5)) if uv else 0.5
        growth = float(getattr(uv, "growth_u",   0.5)) if uv else 0.5
        dur   = float(getattr(util, "duration_secs", 0.0))
        cs    = 1.0 - max(0.0, min(1.0, dur / 300.0))
        roi   = (growth + comp) / 2.0
        conf  = (trust  + comp) / 2.0
        rp    = max(0.0, 1.0 - trust)
        sv    = _strategic_value(roi, conf, cs, rp)
        applied = bool(getattr(util, "applied_to_live", False))
        states["utility"] = ClusterState(
            cluster_id="utility",
            has_result=True,
            lifecycle=ClusterLifecycle.ACTIVE,
            strategic_value=sv,
            roi_score=roi,
            confidence=conf,
            risk_penalty=rp,
            compute_score=cs,
            lot_scale=float(getattr(util, "kelly_lot_scale", 1.0)),
            last_run_secs=dur,
            applied_to_live=applied,
            extra={
                "composite_utility": round(comp, 3),
                "kelly_lot_scale":   round(float(getattr(util, "kelly_lot_scale", 1.0)), 3),
            },
        )
    else:
        states["utility"] = ClusterState(
            cluster_id="utility",
            has_result=False,
            lifecycle=ClusterLifecycle.SUSPENDED,
            extra={},
        )

    # ── Ecosystem / Game-Theory cluster ──────────────────────────────── #
    eco = getattr(app_state, "ecosystem_result", None)
    if eco is not None:
        eco_pf   = float(getattr(eco, "ecosystem_pf", 1.0))
        iso_pf   = float(getattr(eco, "isolation_pf", 1.0))
        expl     = getattr(eco, "exploitability", {}) or {}
        avg_expl = sum(expl.values()) / max(len(expl), 1)
        dur      = float(getattr(eco, "duration_secs", 0.0))
        cs       = 1.0 - max(0.0, min(1.0, dur / 300.0))
        roi      = math.tanh(max(0.0, eco_pf - 1.0))
        conf     = 1.0 - avg_expl
        rp       = min(1.0, avg_expl * 2.0)
        sv       = _strategic_value(roi, conf, cs, rp)
        applied  = bool(getattr(eco, "applied_to_live", False))
        states["ecosystem"] = ClusterState(
            cluster_id="ecosystem",
            has_result=True,
            lifecycle=ClusterLifecycle.ACTIVE,
            strategic_value=sv,
            roi_score=roi,
            confidence=conf,
            risk_penalty=rp,
            compute_score=cs,
            lot_scale=float(getattr(getattr(eco, "best_response_genome", None), "lot_scale", 1.0)
                            if hasattr(eco, "best_response_genome") else 1.0),
            last_run_secs=dur,
            applied_to_live=applied,
            extra={
                "ecosystem_pf":    round(eco_pf, 3),
                "isolation_pf":    round(iso_pf, 3),
                "avg_exploitability": round(avg_expl, 3),
            },
        )
    else:
        states["ecosystem"] = ClusterState(
            cluster_id="ecosystem",
            has_result=False,
            lifecycle=ClusterLifecycle.SUSPENDED,
            extra={},
        )

    return states


def _strategic_value(roi: float, confidence: float,
                     compute_score: float, risk_penalty: float) -> float:
    """
    Composite strategic value ∈ [0, 1].

    Formula:
        sv = 0.40×roi + 0.30×confidence + 0.15×compute_score − 0.15×risk_penalty
    Clipped to [0, 1].
    """
    sv = (
        0.40 * roi
        + 0.30 * confidence
        + 0.15 * compute_score
        - 0.15 * risk_penalty
    )
    return max(0.0, min(1.0, round(sv, 6)))


# ── Internal: Objective Level auto-detection ──────────────────────────────── #

def _detect_objective_level(app_state: Any, policy: SovereignPolicy) -> ObjectiveLevel:
    """
    Auto-detect the appropriate objective level from system risk state.

    Reads from risk_manager if available. Falls back to policy's configured level.
    """
    rm = getattr(app_state, "risk_manager", None)
    if rm is None:
        return policy.objective_level

    dd_obj = getattr(rm, "drawdown", None)
    if dd_obj is None:
        return policy.objective_level

    # If daily drawdown protection triggered → SURVIVAL
    if getattr(dd_obj, "triggered", False):
        return ObjectiveLevel.SURVIVAL

    # Read daily PnL relative to equity for stability detection
    daily_pnl   = float(getattr(dd_obj, "daily_pnl", 0.0))
    equity      = float(getattr(app_state, "equity", 10_000.0))
    daily_dd_pct = (-daily_pnl / max(equity, 1.0)) * 100.0

    max_daily_dd_pct = float(getattr(dd_obj, "max_daily_dd_pct", 5.0))
    if daily_dd_pct >= max_daily_dd_pct * 0.75:
        return ObjectiveLevel.STABILITY

    return policy.objective_level


# ── Internal: Attention Portfolio Optimizer ───────────────────────────────── #

# Risk-aversion parameter λ per objective level.
# Higher λ → heavier penalty on risky clusters in portfolio allocation.
_LAMBDA_BY_OBJECTIVE: Dict[str, float] = {
    ObjectiveLevel.SURVIVAL.value:  5.0,
    ObjectiveLevel.STABILITY.value: 1.5,
    ObjectiveLevel.GROWTH.value:    0.5,
    ObjectiveLevel.DOMINANCE.value: 0.2,
}


def _allocate_resources(
    states: Dict[str, ClusterState],
    policy: SovereignPolicy,
) -> Dict[str, float]:
    """
    Attention Portfolio Optimizer — maximizes network dominance.

    Objective (attention portfolio problem)
    ----------------------------------------
    maximize:   D(α) = Σ_i sv_i × α_i  −  λ × Σ_i risk_i × α_i
    subject to: Σ_i α_i = 1,  0 ≤ α_i ≤ max_attention_per_cluster

    The unconstrained optimum of this LP is to concentrate all weight on
    the cluster with the highest (sv_i − λ × risk_i) excess return.
    With the cap constraint we solve greedily:

    Algorithm
    ---------
    1. Compute excess_return_i = max(0, sv_i − λ × risk_i)
    2. Normalize to obtain raw attention weights
    3. Apply per-cluster cap (max_attention_per_cluster)
    4. Redistribute overflow proportionally to under-cap clusters
    5. Repeat until convergence (max 10 iterations)

    This reduces to the original simple strategy when all excess returns
    are equal, so it is backward-compatible for trivial cases.

    Objective-level modifiers
    --------------------------
    SURVIVAL  : λ=5.0  → only the least-risky cluster survives
    STABILITY : λ=1.5  → strongly penalises high-risk clusters
    GROWTH    : λ=0.5  → moderate risk penalty (default)
    DOMINANCE : λ=0.2  → near-pure sv weighting — reward dominance
    """
    lam = _LAMBDA_BY_OBJECTIVE.get(policy.objective_level.value, 0.5)
    cap = policy.max_attention_per_cluster

    # Compute excess return for each cluster
    excess: Dict[str, float] = {}
    for cid, cs in states.items():
        if not cs.has_result:
            excess[cid] = 0.0
            continue
        er = cs.strategic_value - lam * cs.risk_penalty
        excess[cid] = max(0.0, er)

    total_excess = sum(excess.values())
    if total_excess < _EPS:
        # All clusters have zero or negative excess return
        # Fall back to equal allocation among clusters with results
        n_active = sum(1 for cs in states.values() if cs.has_result)
        if n_active == 0:
            return {cid: 0.0 for cid in states}
        eq = 1.0 / n_active
        return {cid: (eq if states[cid].has_result else 0.0) for cid in states}

    weights: Dict[str, float] = {k: v / total_excess for k, v in excess.items()}

    # Iterative cap redistribution
    for _ in range(10):
        overflow   = sum(max(0.0, w - cap) for w in weights.values())
        if overflow < _EPS:
            break
        weights    = {k: min(w, cap) for k, w in weights.items()}
        under_keys = [k for k, w in weights.items() if w < cap - _EPS]
        if not under_keys:
            break
        per_under  = overflow / len(under_keys)
        weights    = {
            k: min(cap, w + per_under) if k in under_keys else w
            for k, w in weights.items()
        }

    # Normalize to sum 1.0 if requested
    if policy.attention_normalize:
        total = sum(weights.values())
        if total > _EPS:
            weights = {k: v / total for k, v in weights.items()}

    return weights


# ── Internal: Network Dominance Score ────────────────────────────────────── #

def _compute_network_dominance(
    states:    Dict[str, ClusterState],
    allocs:    Dict[str, float],
    prev_raw:  Optional[float],
) -> NetworkDominanceScore:
    """
    Compute total network dominance score for this cycle.

    The portfolio is treated as an attention fund:
      - raw_dominance = Σ(sv_i × α_i) across all clusters
      - portfolio_risk = Σ(risk_i × α_i) weighted risk exposure
      - risk_adjusted_dominance = raw × (1 − portfolio_risk)
      - portfolio_efficiency = raw / max_possible (best-case if all attention
                               went to the single highest-sv cluster)
      - concentration_hhi = Herfindahl index Σ(α_i²) ∈ [1/N, 1]
    """
    active_states = [(cid, cs, allocs.get(cid, 0.0))
                     for cid, cs in states.items()
                     if cs.has_result and allocs.get(cid, 0.0) > _EPS]

    if not active_states:
        nd = NetworkDominanceScore(
            raw_dominance=0.0,
            risk_adjusted_dominance=0.0,
            portfolio_risk=0.0,
            portfolio_efficiency=0.0,
            concentration_hhi=1.0,
            n_active_clusters=0,
            delta_vs_previous=None,
            trajectory="STABLE",
        )
        return nd

    raw       = sum(cs.strategic_value * alpha for _, cs, alpha in active_states)
    port_risk = sum(cs.risk_penalty     * alpha for _, cs, alpha in active_states)
    rad       = raw * (1.0 - port_risk)

    # Efficiency: how close to the theoretical max (all attention on best cluster)
    max_sv = max(cs.strategic_value for _, cs, _ in active_states)
    efficiency = raw / max(max_sv, _EPS)

    # Herfindahl concentration index
    alpha_vals = [alpha for _, _, alpha in active_states]
    hhi = sum(a ** 2 for a in alpha_vals)

    # Trajectory vs previous cycle
    delta: Optional[float] = None
    trajectory = "STABLE"
    if prev_raw is not None:
        delta = round(raw - prev_raw, 6)
        if delta > 0.01:
            trajectory = "IMPROVING"
        elif delta < -0.01:
            trajectory = "DECLINING"

    return NetworkDominanceScore(
        raw_dominance=round(raw, 6),
        risk_adjusted_dominance=round(rad, 6),
        portfolio_risk=round(port_risk, 6),
        portfolio_efficiency=round(efficiency, 6),
        concentration_hhi=round(hhi, 6),
        n_active_clusters=len(active_states),
        delta_vs_previous=delta,
        trajectory=trajectory,
    )


# ── Internal: Objective Level auto-detection ──────────────────────────────── #

def _issue_directives(
    states:    Dict[str, ClusterState],
    allocs:    Dict[str, float],
    policy:    SovereignPolicy,
) -> Dict[str, ClusterDirective]:
    """
    Issue a ClusterDirective for every cluster based on strategic value and policy.

    Decision tree per cluster
    -------------------------
    1. No result              → SUSPEND
    2. sv ≤ kill_threshold    → KILL
    3. sv ≤ throttle_threshold→ THROTTLE
    4. sv ≥ boost_threshold   → SCALE_UP
    5. high confidence / low ROI gap → UPGRADE (under-utilised)
    6. redundant pair detected → MERGE (advisory on weaker peer)
    7. otherwise              → MAINTAIN
    """
    directives: Dict[str, ClusterDirective] = {}

    # Pre-compute merge candidates: clusters with very similar sv and roi
    active_ids = [cid for cid, cs in states.items() if cs.has_result]
    merge_targets: Dict[str, str] = {}  # weaker_cid → stronger_cid
    for i, cid_a in enumerate(active_ids):
        for cid_b in active_ids[i + 1:]:
            cs_a = states[cid_a]
            cs_b = states[cid_b]
            similarity = 1.0 - (abs(cs_a.strategic_value - cs_b.strategic_value)
                                + abs(cs_a.roi_score       - cs_b.roi_score)) / 2.0
            if similarity > 0.85:
                # The weaker one (lower sv) is the merge candidate
                if cs_a.strategic_value <= cs_b.strategic_value:
                    merge_targets[cid_a] = cid_b
                else:
                    merge_targets[cid_b] = cid_a

    for cid, cs in states.items():
        sv       = cs.strategic_value
        att      = allocs.get(cid, 0.0)
        evidence = {
            "strategic_value":  round(sv, 4),
            "roi_score":        round(cs.roi_score, 4),
            "confidence":       round(cs.confidence, 4),
            "risk_penalty":     round(cs.risk_penalty, 4),
            "compute_score":    round(cs.compute_score, 4),
            "current_lot":      round(cs.lot_scale, 3),
            "new_attention":    round(att, 4),
        }

        if not cs.has_result:
            directives[cid] = ClusterDirective(
                cluster_id=cid,
                directive=DirectiveType.SUSPEND,
                new_attention=0.0,
                lot_scale_cap=None,
                rationale=f"{cid}: no result — cluster suspended pending first run",
                evidence_metrics=evidence,
                confidence=1.0,
            )
            continue

        # KILL: sv too low to justify keeping alive
        if sv <= policy.kill_threshold:
            directives[cid] = ClusterDirective(
                cluster_id=cid,
                directive=DirectiveType.KILL,
                new_attention=0.0,
                lot_scale_cap=0.25,
                rationale=(
                    f"{cid}: strategic_value={sv:.3f} ≤ kill_threshold={policy.kill_threshold:.3f}. "
                    f"ROI={cs.roi_score:.3f} confidence={cs.confidence:.3f}. "
                    "Cluster terminated — resource allocation reset to zero."
                ),
                evidence_metrics=evidence,
                confidence=min(1.0, (policy.kill_threshold - sv) / (policy.kill_threshold + _EPS) + 0.5),
            )
        # THROTTLE: sv below throttle threshold
        elif sv <= policy.throttle_threshold:
            directives[cid] = ClusterDirective(
                cluster_id=cid,
                directive=DirectiveType.THROTTLE,
                new_attention=att * 0.5,
                lot_scale_cap=max(0.25, cs.lot_scale * 0.70),
                rationale=(
                    f"{cid}: strategic_value={sv:.3f} ≤ throttle_threshold={policy.throttle_threshold:.3f}. "
                    "Allocation halved; lot_scale capped."
                ),
                evidence_metrics=evidence,
                confidence=min(1.0, (policy.throttle_threshold - sv + 0.10)),
            )
        # SCALE_UP: sv high enough and objective allows it
        elif sv >= policy.boost_threshold:
            lot_cap = min(policy.max_lot_override, cs.lot_scale * 1.20)
            directives[cid] = ClusterDirective(
                cluster_id=cid,
                directive=DirectiveType.SCALE_UP,
                new_attention=min(policy.max_attention_per_cluster, att * 1.20),
                lot_scale_cap=lot_cap,
                rationale=(
                    f"{cid}: strategic_value={sv:.3f} ≥ boost_threshold={policy.boost_threshold:.3f}. "
                    f"ROI={cs.roi_score:.3f} confidence={cs.confidence:.3f}. "
                    "Cluster promoted — attention and lot_scale increased."
                ),
                evidence_metrics=evidence,
                confidence=min(1.0, (sv - policy.boost_threshold + 0.10)),
            )
        # MERGE: cluster is redundant with a higher-value peer (advisory)
        elif cid in merge_targets:
            stronger = merge_targets[cid]
            directives[cid] = ClusterDirective(
                cluster_id=cid,
                directive=DirectiveType.MERGE,
                new_attention=att * 0.30,  # reduce attention but don't kill yet
                lot_scale_cap=cs.lot_scale,  # no lot change
                rationale=(
                    f"{cid}: strategic_value={sv:.3f} is highly similar to [{stronger}] "
                    f"(sv={states[stronger].strategic_value:.3f}). "
                    "ADVISORY: consider merging this cluster's learned weights into "
                    f"[{stronger}] and consolidating attention budget."
                ),
                evidence_metrics={
                    **evidence,
                    "merge_target":          stronger,
                    "target_sv":             round(states[stronger].strategic_value, 4),
                    "similarity_gap":        round(abs(sv - states[stronger].strategic_value), 4),
                },
                confidence=0.65,
            )
        # UPGRADE: high confidence but under-utilised ROI
        elif cs.confidence > _UPGRADE_MIN_CONFIDENCE and cs.roi_score < _UPGRADE_MAX_ROI and sv > policy.kill_threshold:
            sv_gap = cs.confidence - cs.roi_score
            directives[cid] = ClusterDirective(
                cluster_id=cid,
                directive=DirectiveType.UPGRADE,
                new_attention=min(policy.max_attention_per_cluster, att * 1.10),
                lot_scale_cap=min(policy.max_lot_override, cs.lot_scale * 1.15),
                rationale=(
                    f"{cid}: confidence={cs.confidence:.3f} >> roi_score={cs.roi_score:.3f} "
                    f"(gap={sv_gap:.3f}). Cluster is reliable but under-utilised. "
                    "UPGRADE: run with more aggressive parameters to unlock latent ROI."
                ),
                evidence_metrics={
                    **evidence,
                    "confidence_roi_gap": round(sv_gap, 4),
                },
                confidence=min(1.0, 0.50 + sv_gap),
            )
        # MAINTAIN: everything nominal
        else:
            directives[cid] = ClusterDirective(
                cluster_id=cid,
                directive=DirectiveType.MAINTAIN,
                new_attention=att,
                lot_scale_cap=None,
                rationale=(
                    f"{cid}: strategic_value={sv:.3f} within nominal range. "
                    "No governance action required."
                ),
                evidence_metrics=evidence,
                confidence=0.80,
            )

    return directives


# ── Internal: Governance Insights ────────────────────────────────────────── #

def _build_insights(
    states:     Dict[str, ClusterState],
    directives: Dict[str, ClusterDirective],
    allocs:     Dict[str, float],
    obj_tree:   NetworkObjectiveTree,
    policy:     SovereignPolicy,
    dominance:  "NetworkDominanceScore",
) -> List[str]:
    insights: List[str] = []

    # Network dominance headline
    traj_icon = {"IMPROVING": "📈", "DECLINING": "📉", "STABLE": "➡️"}.get(dominance.trajectory, "➡️")
    delta_str = (f" Δ{dominance.delta_vs_previous:+.4f}" if dominance.delta_vs_previous is not None else "")
    insights.append(
        f"🌐 Network dominance: raw={dominance.raw_dominance:.4f} "
        f"risk-adj={dominance.risk_adjusted_dominance:.4f} "
        f"efficiency={dominance.portfolio_efficiency:.4f} "
        f"HHI={dominance.concentration_hhi:.3f} "
        f"{traj_icon} {dominance.trajectory}{delta_str}"
    )

    # Objective level
    insights.append(
        f"🏛️ Sovereign objective: {obj_tree.active_level.value} "
        f"(survival_triggered={obj_tree.survival_triggered})"
    )

    # Cluster overview
    active    = [cid for cid, cs in states.items() if cs.lifecycle == ClusterLifecycle.ACTIVE]
    suspended = [cid for cid, cs in states.items() if cs.lifecycle == ClusterLifecycle.SUSPENDED]
    insights.append(
        f"📊 Cluster health: {obj_tree.healthy_clusters}/{obj_tree.total_clusters} active "
        f"({', '.join(active) or 'none'})"
    )
    if suspended:
        insights.append(f"⏸️ Suspended (no result): {', '.join(suspended)}")

    # Directive summary
    kills     = [cid for cid, d in directives.items() if d.directive == DirectiveType.KILL]
    throttles = [cid for cid, d in directives.items() if d.directive == DirectiveType.THROTTLE]
    scales    = [cid for cid, d in directives.items() if d.directive == DirectiveType.SCALE_UP]
    maintains = [cid for cid, d in directives.items() if d.directive == DirectiveType.MAINTAIN]
    upgrades  = [cid for cid, d in directives.items() if d.directive == DirectiveType.UPGRADE]
    merges    = [cid for cid, d in directives.items() if d.directive == DirectiveType.MERGE]

    if kills:
        insights.append(f"💀 KILL directives: {', '.join(kills)} — below kill threshold {policy.kill_threshold:.2f}")
    if throttles:
        insights.append(f"🔽 THROTTLE directives: {', '.join(throttles)} — allocation halved")
    if scales:
        insights.append(f"🚀 SCALE_UP directives: {', '.join(scales)} — high strategic value")
    if upgrades:
        insights.append(
            f"⬆️  UPGRADE directives: {', '.join(upgrades)} — high confidence but under-utilised ROI; "
            "run with more aggressive parameters"
        )
    if merges:
        for cid in merges:
            target = directives[cid].evidence_metrics.get("merge_target", "?")
            insights.append(
                f"🔀 MERGE advisory: [{cid}] → [{target}] — clusters are redundant; "
                "consider consolidating learned weights and attention budget"
            )
    if maintains:
        insights.append(f"✅ MAINTAIN: {', '.join(maintains)} — nominal performance")

    # Top-scoring cluster
    active_states = [(cid, cs) for cid, cs in states.items() if cs.has_result]
    if active_states:
        best_cid, best_cs = max(active_states, key=lambda x: x[1].strategic_value)
        insights.append(
            f"🏆 Top cluster: [{best_cid}] sv={best_cs.strategic_value:.3f} "
            f"roi={best_cs.roi_score:.3f} attention={allocs.get(best_cid, 0.0):.3f}"
        )

    # Portfolio concentration warning
    if dominance.concentration_hhi > 0.70:
        insights.append(
            f"⚠️ High attention concentration (HHI={dominance.concentration_hhi:.3f}) — "
            "portfolio is over-concentrated in one cluster. Consider raising max_attention_per_cluster cap."
        )

    # Guardrail notice
    if policy.mode == SovereignMode.ADVISORY:
        insights.append(
            "📋 Mode: ADVISORY — directives computed but NOT applied to live system. "
            "Call POST /api/sovereign/apply to enforce."
        )
    elif policy.mode == SovereignMode.SEMI_AUTO:
        insights.append(
            "⚠️ Mode: SEMI_AUTO — MAINTAIN/THROTTLE auto-applied; KILL/SCALE_UP require /apply."
        )
    else:
        insights.append("⚡ Mode: FULL_AUTO — all directives applied immediately.")

    return insights


# ── SovereignOversightEngine ─────────────────────────────────────────────── #

class SovereignOversightEngine:
    """
    STRATEGIC SOVEREIGN OVERSIGHT LAYER — the top of the intelligence stack.

    Governs the entire engine ecosystem as a network-level OS:
      • Sets network objectives (SURVIVAL → STABILITY → GROWTH → DOMINANCE)
      • Allocates attention budget across engine clusters
      • Issues governance directives (SCALE_UP / THROTTLE / SUSPEND / KILL / MAINTAIN)
      • Enforces guardrails so sovereign decisions never override risk hard-limits
      • Maintains a full audit trail for replay and post-analysis

    Usage
    -----
    engine = SovereignOversightEngine(policy=SovereignPolicy())
    result = engine.run(app_state)    # app_state from FastAPI AppState
    result.apply_to(app_state)        # apply directives to live system
    """

    def __init__(self, policy: Optional[SovereignPolicy] = None) -> None:
        self.policy: SovereignPolicy = policy or SovereignPolicy()
        self._audit_trail: List[AuditEntry] = []
        self._last_result: Optional[SovereignOversightResult] = None
        self._cycle_counter: int = 0
        self._prev_raw_dominance: Optional[float] = None  # for trajectory tracking

    @property
    def last_result(self) -> Optional[SovereignOversightResult]:
        return self._last_result

    def update_policy(self, new_policy: SovereignPolicy) -> None:
        """Hot-swap the sovereign policy without re-running."""
        self.policy = new_policy
        logger.info(
            "SovereignOversightEngine: policy updated → mode=%s objective=%s",
            new_policy.mode.value, new_policy.objective_level.value,
        )

    def run(self, app_state: Any) -> SovereignOversightResult:
        """
        Execute one sovereign oversight cycle.

        Pipeline
        --------
        Phase 1 — Collect telemetry from all engine clusters
        Phase 2 — Auto-detect objective level from system risk state
        Phase 3 — Allocate attention budget
        Phase 4 — Issue governance directives
        Phase 5 — Build insights + objective tree snapshot
        Phase 6 — Append audit trail entries
        Phase 7 — (FULL_AUTO only) auto-apply directives to live system

        Returns SovereignOversightResult.
        """
        t0 = time.time()
        self._cycle_counter += 1
        cycle_id = f"sov-{int(t0)}-{self._cycle_counter:04d}"
        logger.info("SovereignOversightEngine.run: cycle=%s", cycle_id)

        # Phase 1 — Telemetry
        states = _collect_telemetry(app_state)

        # Phase 2 — Auto-detect objective level (respects risk hard-limits)
        effective_obj = _detect_objective_level(app_state, self.policy)
        policy_effective = SovereignPolicy(
            mode                     = self.policy.mode,
            objective_level          = effective_obj,
            max_lot_override         = self.policy.max_lot_override,
            min_lot_override         = self.policy.min_lot_override,
            kill_threshold           = self.policy.kill_threshold,
            throttle_threshold       = self.policy.throttle_threshold,
            boost_threshold          = self.policy.boost_threshold,
            attention_normalize      = self.policy.attention_normalize,
            max_attention_per_cluster= self.policy.max_attention_per_cluster,
        )

        # Override thresholds if SURVIVAL triggered
        if effective_obj == ObjectiveLevel.SURVIVAL:
            policy_effective.kill_threshold      = 0.40
            policy_effective.throttle_threshold  = 0.60
            policy_effective.boost_threshold     = 0.95
            policy_effective.max_lot_override    = min(self.policy.max_lot_override, 0.25)

        # Phase 3 — Allocate attention budget
        allocs = _allocate_resources(states, policy_effective)

        # Phase 4 — Issue directives
        directives = _issue_directives(states, allocs, policy_effective)

        # Update lifecycle state on cluster states
        for cid, d in directives.items():
            if d.directive == DirectiveType.KILL:
                states[cid].lifecycle = ClusterLifecycle.KILLED
            elif d.directive == DirectiveType.THROTTLE:
                states[cid].lifecycle = ClusterLifecycle.THROTTLED
            elif d.directive == DirectiveType.SUSPEND:
                states[cid].lifecycle = ClusterLifecycle.SUSPENDED
            else:
                if states[cid].has_result:
                    states[cid].lifecycle = ClusterLifecycle.ACTIVE
            states[cid].attention_budget = d.new_attention

        # Phase 5 — Compute network dominance score
        prev_raw = self._prev_raw_dominance
        network_dominance = _compute_network_dominance(states, allocs, prev_raw)

        # Phase 5b — Build objective tree snapshot
        healthy   = sum(1 for cs in states.values() if cs.lifecycle == ClusterLifecycle.ACTIVE)
        sv_values = [cs.strategic_value for cs in states.values() if cs.has_result]
        stability  = float(sum(cs.confidence for cs in states.values() if cs.has_result)
                           / max(sum(1 for cs in states.values() if cs.has_result), 1))
        growth_s   = float(sum(cs.roi_score for cs in states.values() if cs.has_result)
                           / max(sum(1 for cs in states.values() if cs.has_result), 1))
        dominance_s = max(sv_values) if sv_values else 0.0

        obj_tree = NetworkObjectiveTree(
            active_level       = effective_obj,
            survival_triggered = effective_obj == ObjectiveLevel.SURVIVAL,
            stability_score    = stability,
            growth_score       = growth_s,
            dominance_score    = dominance_s,
            total_attention    = sum(allocs.values()),
            healthy_clusters   = healthy,
            total_clusters     = len(states),
        )

        insights = _build_insights(states, directives, allocs, obj_tree, policy_effective, network_dominance)

        # Phase 6 — Audit trail
        ts = time.time()
        new_audit: List[AuditEntry] = []
        for cid, d in directives.items():
            entry = AuditEntry(
                cycle_id       = cycle_id,
                timestamp      = ts,
                cluster_id     = cid,
                directive      = d.directive.value,
                rationale      = d.rationale,
                objective_level= effective_obj.value,
                sovereign_mode = policy_effective.mode.value,
                applied        = False,  # updated after apply_to
            )
            new_audit.append(entry)
        self._audit_trail.extend(new_audit)
        # Keep audit bounded (last 500 entries)
        if len(self._audit_trail) > 500:
            self._audit_trail = self._audit_trail[-500:]

        result = SovereignOversightResult(
            cycle_id            = cycle_id,
            cluster_states      = states,
            directives          = directives,
            resource_allocation = allocs,
            sovereign_policy    = policy_effective,
            objective_tree      = obj_tree,
            network_dominance   = network_dominance,
            governance_insights = insights,
            audit_trail         = list(self._audit_trail),
            duration_secs       = time.time() - t0,
        )
        self._last_result = result
        self._prev_raw_dominance = network_dominance.raw_dominance

        # Phase 7 — FULL_AUTO: apply immediately
        if policy_effective.mode == SovereignMode.FULL_AUTO:
            result.apply_to(app_state)
            for entry in new_audit:
                entry.applied = True

        logger.info(
            "SovereignOversightEngine.run: cycle=%s objective=%s healthy=%d/%d "
            "dominance=%.4f(%s) kills=%d throttles=%d scales=%d upgrades=%d dur=%.2fs",
            cycle_id,
            effective_obj.value,
            healthy,
            len(states),
            network_dominance.raw_dominance,
            network_dominance.trajectory,
            sum(1 for d in directives.values() if d.directive == DirectiveType.KILL),
            sum(1 for d in directives.values() if d.directive == DirectiveType.THROTTLE),
            sum(1 for d in directives.values() if d.directive == DirectiveType.SCALE_UP),
            sum(1 for d in directives.values() if d.directive == DirectiveType.UPGRADE),
            result.duration_secs,
        )
        return result
