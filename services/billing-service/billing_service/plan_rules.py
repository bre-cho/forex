"""Plan rules — defines what each subscription plan allows."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict


class Plan(str, Enum):
    FREE = "free"
    STARTER = "starter"
    PRO = "pro"
    ENTERPRISE = "enterprise"


@dataclass
class PlanLimits:
    max_bots: int
    max_symbols: int
    live_trading: bool
    ai_features: bool
    api_access: bool
    priority_support: bool


PLAN_RULES: Dict[Plan, PlanLimits] = {
    Plan.FREE: PlanLimits(
        max_bots=1, max_symbols=1,
        live_trading=False, ai_features=False,
        api_access=False, priority_support=False,
    ),
    Plan.STARTER: PlanLimits(
        max_bots=3, max_symbols=3,
        live_trading=True, ai_features=False,
        api_access=False, priority_support=False,
    ),
    Plan.PRO: PlanLimits(
        max_bots=10, max_symbols=10,
        live_trading=True, ai_features=True,
        api_access=True, priority_support=False,
    ),
    Plan.ENTERPRISE: PlanLimits(
        max_bots=9999, max_symbols=9999,
        live_trading=True, ai_features=True,
        api_access=True, priority_support=True,
    ),
}


def get_plan_limits(plan: str) -> PlanLimits:
    return PLAN_RULES.get(Plan(plan), PLAN_RULES[Plan.FREE])
