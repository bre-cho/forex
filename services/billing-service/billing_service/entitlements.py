"""Entitlements — checks whether a user/workspace is entitled to a feature."""
from __future__ import annotations

from .plan_rules import Plan, get_plan_limits


class EntitlementService:
    """Checks feature access against plan limits."""

    def can_create_bot(self, plan: str, current_bot_count: int) -> bool:
        limits = get_plan_limits(plan)
        return current_bot_count < limits.max_bots

    def can_use_live_trading(self, plan: str) -> bool:
        return get_plan_limits(plan).live_trading

    def can_use_ai_features(self, plan: str) -> bool:
        return get_plan_limits(plan).ai_features

    def can_use_api_access(self, plan: str) -> bool:
        return get_plan_limits(plan).api_access

    def can_add_symbol(self, plan: str, current_symbol_count: int) -> bool:
        limits = get_plan_limits(plan)
        return current_symbol_count < limits.max_symbols

    def get_limits(self, plan: str) -> dict:
        limits = get_plan_limits(plan)
        return {
            "max_bots": limits.max_bots,
            "max_symbols": limits.max_symbols,
            "live_trading": limits.live_trading,
            "ai_features": limits.ai_features,
            "api_access": limits.api_access,
            "priority_support": limits.priority_support,
        }
