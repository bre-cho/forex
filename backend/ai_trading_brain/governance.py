from __future__ import annotations
from typing import Any, Dict, Tuple


class TradingBrainGovernance:
    """Production guardrail: VALID=ALLOW, INVALID=BLOCK, UNCERTAIN=SKIP, DEFAULT=DENY."""

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = {
            "max_daily_loss_pct": 5.0,
            "max_consecutive_losses": 4,
            "kill_switch": False,
            "require_broker_connected": True,
            **(config or {}),
        }

    def preflight(self, context: Dict[str, Any]) -> Tuple[bool, str]:
        if self.config.get("kill_switch"):
            return False, "kill_switch_enabled"
        if self.config.get("require_broker_connected") and not context.get("broker_connected", False):
            return False, "broker_not_connected"
        if float(context.get("daily_loss_pct", 0.0)) >= float(self.config["max_daily_loss_pct"]):
            return False, "daily_loss_limit_hit"
        if int(context.get("consecutive_losses", 0)) >= int(self.config["max_consecutive_losses"]):
            return False, "consecutive_loss_limit_hit"
        if context.get("market_data_ok") is False:
            return False, "market_data_invalid"
        return True, "ok"
