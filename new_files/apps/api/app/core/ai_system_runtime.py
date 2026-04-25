"""
AI System Runtime — reusable operator layer inspired by ai_system_full_pack.

Purpose
-------
Turn ad-hoc API/runtime actions into a validated execution envelope:
Goal -> Inputs -> Skill -> Tool/Runtime -> Validation -> Output.

This module is deliberately dependency-light so it can be used by routers,
workers, and runtime services without creating import cycles.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional


class Decision(str, Enum):
    ALLOW = "allow"
    REVIEW = "review"
    BLOCK = "block"


class Skill(str, Enum):
    TRADING_RUNTIME = "trading_runtime"
    RISK_REVIEW = "risk_review"
    BROKER_HEALTH = "broker_health"
    STRATEGY_VALIDATION = "strategy_validation"
    INCIDENT_REVIEW = "incident_review"


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    severity: str = "warning"  # info | warning | error


@dataclass
class ValidationReport:
    decision: Decision
    confidence: float
    issues: List[ValidationIssue] = field(default_factory=list)
    next_actions: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.decision == Decision.ALLOW

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision.value,
            "allowed": self.allowed,
            "confidence": round(float(self.confidence), 4),
            "issues": [issue.__dict__ for issue in self.issues],
            "next_actions": list(self.next_actions),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ExecutionEnvelope:
    goal: str
    inputs: Mapping[str, Any]
    skill: Skill
    context: Mapping[str, Any] = field(default_factory=dict)
    requires_external_data: bool = False
    production_critical: bool = True


class AISystemRuntime:
    """Small validation/orchestration kernel for production actions."""

    def validate_envelope(self, envelope: ExecutionEnvelope) -> ValidationReport:
        issues: List[ValidationIssue] = []
        next_actions: List[str] = []

        if not envelope.goal.strip():
            issues.append(ValidationIssue("missing_goal", "Execution goal is empty", "error"))
        if not envelope.inputs:
            issues.append(ValidationIssue("missing_inputs", "No input data supplied", "error"))
        if envelope.requires_external_data and not envelope.context.get("data_source_verified"):
            issues.append(
                ValidationIssue(
                    "unverified_external_data",
                    "External data is required but no verified data source is marked",
                    "error",
                )
            )
            next_actions.append("Verify broker/feed/API source before executing")
        if envelope.production_critical and not envelope.context.get("validation_passed"):
            issues.append(
                ValidationIssue(
                    "validation_required",
                    "Production-critical action must pass validation first",
                    "warning",
                )
            )
            next_actions.append("Run pre-flight validation and attach validation_passed=true")

        errors = [i for i in issues if i.severity == "error"]
        warnings = [i for i in issues if i.severity == "warning"]
        if errors:
            decision = Decision.BLOCK
            confidence = 0.0
        elif warnings:
            decision = Decision.REVIEW
            confidence = 0.65
        else:
            decision = Decision.ALLOW
            confidence = 0.95

        return ValidationReport(
            decision=decision,
            confidence=confidence,
            issues=issues,
            next_actions=next_actions,
            metadata={"skill": envelope.skill.value},
        )


SYSTEM_MANIFEST: Dict[str, Any] = {
    "identity": "AI_SYSTEM_FULL_OPERATOR_FOR_FOREX",
    "core_goal": "Model + Prompt + Skills + Tools + Context + Validation + Production Loop",
    "operating_laws": [
        "unclear_goal_requires_assumption_or_clarification",
        "no_data_no_certainty",
        "repeated_work_becomes_skill",
        "external_data_requires_tool_or_api",
        "critical_output_requires_validation",
        "clarity_and_correctness_over_style",
    ],
    "pipeline": [
        "identify_goal",
        "identify_available_inputs",
        "identify_missing_inputs",
        "select_skill_or_workflow",
        "select_tool_or_context",
        "execute_stepwise",
        "validate_logic_format_risk",
        "return_result_and_next_step",
    ],
    "default_skills": [s.value for s in Skill],
}


ai_system_runtime = AISystemRuntime()
