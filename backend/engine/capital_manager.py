"""
Capital Manager — Auto-tune trading parameters by account size.

Capital profiles (balance brackets)
-------------------------------------
  MICRO   : balance <  1 000 $   — very conservative, micro lots
  SMALL   : balance <  5 000 $   — conservative, mini lots
  MEDIUM  : balance < 25 000 $   — balanced
  LARGE   : balance ≥ 25 000 $   — aggressive growth possible
  CUSTOM  : user-defined, no auto-adjustment
  AUTO    : auto-detect bracket from live balance

Each profile returns recommended overrides for:
  lot_mode, lot_value, min_lot, max_lot,
  max_daily_dd_pct, max_overall_dd_pct, max_trades_at_time,
  daily_profit_target (suggested), daily_loss_limit (suggested)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class CapitalProfileParams:
    """Recommended risk parameters for one capital bracket."""
    profile:             str
    lot_mode:            str
    lot_value:           float   # lot size (STATIC) or % risk (DYNAMIC_PERCENT)
    min_lot:             float
    max_lot:             float
    max_daily_dd_pct:    float   # % of starting day balance
    max_overall_dd_pct:  float   # % of account peak
    max_trades_at_time:  int
    daily_profit_target: float   # suggested $ target (0 = off, caller may override)
    daily_loss_limit:    float   # suggested $ loss limit (0 = off)
    description:         str


# ── Profile definitions ────────────────────────────────────────────────── #

_PROFILES: Dict[str, CapitalProfileParams] = {
    "MICRO": CapitalProfileParams(
        profile="MICRO",
        lot_mode="DYNAMIC_PERCENT",
        lot_value=1.0,           # 1% risk per trade
        min_lot=0.01,
        max_lot=0.1,
        max_daily_dd_pct=3.0,
        max_overall_dd_pct=10.0,
        max_trades_at_time=1,
        daily_profit_target=0.0,
        daily_loss_limit=0.0,
        description="Balance < $1 000 — micro lots, strict risk control",
    ),
    "SMALL": CapitalProfileParams(
        profile="SMALL",
        lot_mode="DYNAMIC_PERCENT",
        lot_value=1.5,           # 1.5% risk per trade
        min_lot=0.01,
        max_lot=0.5,
        max_daily_dd_pct=4.0,
        max_overall_dd_pct=15.0,
        max_trades_at_time=2,
        daily_profit_target=0.0,
        daily_loss_limit=0.0,
        description="Balance $1 000–$5 000 — conservative mini lots",
    ),
    "MEDIUM": CapitalProfileParams(
        profile="MEDIUM",
        lot_mode="DYNAMIC_PERCENT",
        lot_value=2.0,           # 2% risk per trade
        min_lot=0.01,
        max_lot=2.0,
        max_daily_dd_pct=5.0,
        max_overall_dd_pct=20.0,
        max_trades_at_time=3,
        daily_profit_target=0.0,
        daily_loss_limit=0.0,
        description="Balance $5 000–$25 000 — balanced growth",
    ),
    "LARGE": CapitalProfileParams(
        profile="LARGE",
        lot_mode="DYNAMIC_PERCENT",
        lot_value=1.5,           # 1.5% risk per trade (lower % for large capital)
        min_lot=0.1,
        max_lot=10.0,
        max_daily_dd_pct=3.0,
        max_overall_dd_pct=10.0,
        max_trades_at_time=4,
        daily_profit_target=0.0,
        daily_loss_limit=0.0,
        description="Balance ≥ $25 000 — scale with lower % risk per trade",
    ),
    "CUSTOM": CapitalProfileParams(
        profile="CUSTOM",
        lot_mode="STATIC",
        lot_value=0.01,
        min_lot=0.01,
        max_lot=10.0,
        max_daily_dd_pct=5.0,
        max_overall_dd_pct=20.0,
        max_trades_at_time=3,
        daily_profit_target=0.0,
        daily_loss_limit=0.0,
        description="Manual settings — no auto-adjustment",
    ),
}


class CapitalManager:
    """
    Auto-tune risk parameters based on account balance.

    Usage
    -----
    1. ``detect(balance)``   → returns the appropriate CapitalProfileParams
    2. ``apply(settings, balance)`` → returns an updated settings dict with
       recommended overrides (does not mutate settings in place).
    3. ``suggest_daily_targets(balance, daily_win_rate)`` → suggests profit/loss
       targets based on profile and recent performance.
    """

    def detect(self, balance: float) -> CapitalProfileParams:
        """Detect which capital bracket applies for the given balance."""
        if balance < 1_000:
            return _PROFILES["MICRO"]
        elif balance < 5_000:
            return _PROFILES["SMALL"]
        elif balance < 25_000:
            return _PROFILES["MEDIUM"]
        else:
            return _PROFILES["LARGE"]

    def get_profile(self, name: str) -> CapitalProfileParams:
        """Return profile by name; falls back to CUSTOM if unknown."""
        return _PROFILES.get(name.upper(), _PROFILES["CUSTOM"])

    def apply(
        self,
        settings_dict: dict,
        balance: float,
        profile_override: str = "AUTO",
    ) -> dict:
        """
        Return a copy of *settings_dict* with risk parameters overridden
        according to the capital profile.

        In CUSTOM mode or when the user has set profile_override='CUSTOM',
        no changes are made.
        """
        name = profile_override.upper()
        if name == "CUSTOM":
            return dict(settings_dict)

        if name == "AUTO":
            profile = self.detect(balance)
        else:
            profile = self.get_profile(name)

        overrides: dict = {
            "lot_mode":            profile.lot_mode,
            "lot_value":           profile.lot_value,
            "min_lot":             profile.min_lot,
            "max_lot":             profile.max_lot,
            "max_daily_dd_pct":    profile.max_daily_dd_pct,
            "max_overall_dd_pct":  profile.max_overall_dd_pct,
            "max_trades_at_time":  profile.max_trades_at_time,
        }
        # Only auto-set daily targets when they have not been explicitly
        # configured by the user (i.e. remain at 0 / disabled).
        if settings_dict.get("daily_profit_target", 0.0) == 0.0 and profile.daily_profit_target > 0:
            overrides["daily_profit_target"] = profile.daily_profit_target
        if settings_dict.get("daily_loss_limit", 0.0) == 0.0 and profile.daily_loss_limit > 0:
            overrides["daily_loss_limit"] = profile.daily_loss_limit

        updated = dict(settings_dict)
        updated.update(overrides)
        logger.info(
            "CapitalManager: profile=%s balance=%.2f → lot_mode=%s lot_value=%.3f "
            "max_lot=%.2f dd_daily=%.1f%% dd_overall=%.1f%%",
            profile.profile, balance,
            profile.lot_mode, profile.lot_value, profile.max_lot,
            profile.max_daily_dd_pct, profile.max_overall_dd_pct,
        )
        return updated

    def suggest_daily_targets(
        self,
        balance: float,
        profile_name: str = "AUTO",
        recent_win_rate: float = 0.5,
        avg_trade_pnl: float = 0.0,
    ) -> Dict[str, float]:
        """
        Suggest daily profit target and loss limit based on balance and performance.

        Returns ``{"daily_profit_target": X, "daily_loss_limit": Y}`` in $ terms.
        Returns 0 (disabled) when insufficient data.
        """
        name = profile_name.upper()
        profile = self.detect(balance) if name == "AUTO" else self.get_profile(name)

        # Suggested profit target: daily_dd_pct × balance (scaled by win rate bonus)
        win_bonus = max(1.0, recent_win_rate * 2.0)
        profit_target = round(balance * (profile.max_daily_dd_pct / 100.0) * win_bonus, 2)

        # Suggested loss limit: max_daily_dd_pct × balance
        loss_limit = round(balance * (profile.max_daily_dd_pct / 100.0), 2)

        return {
            "daily_profit_target": profit_target,
            "daily_loss_limit":    loss_limit,
        }
