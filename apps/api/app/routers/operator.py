"""Operator router — exposes AI_SYSTEM_FULL manifest and validation endpoint."""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.ai_system_runtime import (
    ExecutionEnvelope,
    Skill,
    SYSTEM_MANIFEST,
    ai_system_runtime,
)

router = APIRouter(prefix="/v1/operator", tags=["operator"])


class ValidateRequest(BaseModel):
    goal: str = Field(..., min_length=1)
    inputs: Dict[str, Any] = Field(default_factory=dict)
    skill: Skill = Skill.TRADING_RUNTIME
    context: Dict[str, Any] = Field(default_factory=dict)
    requires_external_data: bool = False
    production_critical: bool = True


@router.get("/manifest")
async def manifest() -> Dict[str, Any]:
    return SYSTEM_MANIFEST


@router.post("/validate")
async def validate(body: ValidateRequest) -> Dict[str, Any]:
    report = ai_system_runtime.validate_envelope(
        ExecutionEnvelope(
            goal=body.goal,
            inputs=body.inputs,
            skill=body.skill,
            context=body.context,
            requires_external_data=body.requires_external_data,
            production_critical=body.production_critical,
        )
    )
    return report.to_dict()
