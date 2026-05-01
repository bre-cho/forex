"""
Capital Manager — Auto-tune trading parameters by account size.

Capital profiles (balance brackets)
-------------------------------------
  NANO_500 : balance <    600 $   — ultra-conservative for ~$500 accounts
  NANO_600 : balance <    700 $   — ultra-conservative for ~$600 accounts
  NANO_700 : balance <    800 $   — ultra-conservative for ~$700 accounts
  NANO_800 : balance <    900 $   — ultra-conservative for ~$800 accounts
  NANO_900 : balance <  1 000 $   — ultra-conservative for ~$900 accounts
  MICRO    : balance <  1 000 $   — conservative (selectable alias, AUTO uses NANO)
  SMALL    : balance <  5 000 $   — conservative, mini lots
  MEDIUM   : balance < 25 000 $   — balanced
  LARGE    : balance ≥ 25 000 $   — aggressive growth possible
  CUSTOM   : user-defined, no auto-adjustment
  AUTO     : auto-detect bracket from live balance

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
    # ── NANO sub-profiles for $500–$900 accounts ──────────────────────── #
    # Risk is kept to 0.5–1% per trade; only micro lots (0.01–0.07);
    # daily DD limit 2–3%; a single open trade at a time.
    "NANO_500": CapitalProfileParams(
        profile="NANO_500",
        lot_mode="DYNAMIC_PERCENT",
        lot_value=0.5,           # 0.5% risk per trade (~$2.50 on $500)
        min_lot=0.01,
        max_lot=0.01,
        max_daily_dd_pct=2.0,
        max_overall_dd_pct=8.0,
        max_trades_at_time=1,
        daily_profit_target=0.0,
        daily_loss_limit=0.0,
        description="Balance ~$500 (< $600) — ultra-strict, 0.01 lot only, 0.5% risk/trade",
    ),
    "NANO_600": CapitalProfileParams(
        profile="NANO_600",
        lot_mode="DYNAMIC_PERCENT",
        lot_value=0.7,           # 0.7% risk per trade (~$4.20 on $600)
        min_lot=0.01,
        max_lot=0.02,
        max_daily_dd_pct=2.0,
        max_overall_dd_pct=8.0,
        max_trades_at_time=1,
        daily_profit_target=0.0,
        daily_loss_limit=0.0,
        description="Balance ~$600 ($600–$699) — ultra-strict, max 0.02 lot, 0.7% risk/trade",
    ),
    "NANO_700": CapitalProfileParams(
        profile="NANO_700",
        lot_mode="DYNAMIC_PERCENT",
        lot_value=0.8,           # 0.8% risk per trade (~$5.60 on $700)
        min_lot=0.01,
        max_lot=0.03,
        max_daily_dd_pct=2.5,
        max_overall_dd_pct=9.0,
        max_trades_at_time=1,
        daily_profit_target=0.0,
        daily_loss_limit=0.0,
        description="Balance ~$700 ($700–$799) — strict, max 0.03 lot, 0.8% risk/trade",
    ),
    "NANO_800": CapitalProfileParams(
        profile="NANO_800",
        lot_mode="DYNAMIC_PERCENT",
        lot_value=1.0,           # 1.0% risk per trade (~$8 on $800)
        min_lot=0.01,
        max_lot=0.05,
        max_daily_dd_pct=2.5,
        max_overall_dd_pct=9.0,
        max_trades_at_time=1,
        daily_profit_target=0.0,
        daily_loss_limit=0.0,
        description="Balance ~$800 ($800–$899) — strict, max 0.05 lot, 1.0% risk/trade",
    ),
    "NANO_900": CapitalProfileParams(
        profile="NANO_900",
        lot_mode="DYNAMIC_PERCENT",
        lot_value=1.0,           # 1.0% risk per trade (~$9 on $900)
        min_lot=0.01,
        max_lot=0.07,
        max_daily_dd_pct=3.0,
        max_overall_dd_pct=10.0,
        max_trades_at_time=1,
        daily_profit_target=0.0,
        daily_loss_limit=0.0,
        description="Balance ~$900 ($900–$999) — strict, max 0.07 lot, 1.0% risk/trade",
    ),
    # ── Standard profiles ─────────────────────────────────────────────── #
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
        description="Balance < $1,000 (general) — micro lots, strict risk control",
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
        description="Balance $1,000–$5,000 — conservative mini lots",
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
        description="Balance $5,000–$25,000 — balanced growth",
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
        description="Balance ≥ $25,000 — scale with lower % risk per trade",
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

# Ordered list of NANO profile keys (ascending balance)
_NANO_PROFILES = ["NANO_500", "NANO_600", "NANO_700", "NANO_800", "NANO_900"]


class CapitalManager:
    """
    Auto-tune risk parameters based on account balance.

    Usage
    -----
    1. ``detect(balance)``   → returns the appropriate CapitalProfileParams.
       For balance < $1,000 the finer-grained NANO_500/600/700/800/900 sub-profiles
       are used by AUTO detection; MICRO remains selectable as a named profile.
    2. ``apply(settings, balance)`` → returns an updated settings dict with
       recommended overrides (does not mutate settings in place).
    3. ``suggest_daily_targets(balance, daily_win_rate)`` → suggests profit/loss
       targets based on profile and recent performance.
    """

    def detect(self, balance: float) -> CapitalProfileParams:
        """
        Detect which capital bracket applies for the given balance.

        Sub-$1,000 accounts use fine-grained NANO profiles (100$ increments).
        """
        if balance < 600:
            return _PROFILES["NANO_500"]
        elif balance < 700:
            return _PROFILES["NANO_600"]
        elif balance < 800:
            return _PROFILES["NANO_700"]
        elif balance < 900:
            return _PROFILES["NANO_800"]
        elif balance < 1_000:
            return _PROFILES["NANO_900"]
        elif balance < 5_000:
            return _PROFILES["SMALL"]
        elif balance < 25_000:
            return _PROFILES["MEDIUM"]
        else:
            return _PROFILES["LARGE"]

    def get_profile(self, name: str) -> CapitalProfileParams:
        """Return profile by name; falls back to CUSTOM if unknown."""
        return _PROFILES.get(name.upper(), _PROFILES["CUSTOM"])

    # Absolute hard cap on max_lot expressed as a fraction of equity.
    # Daily compounding after a winning streak can push lot sizes far beyond
    # what is prudent.  This cap is applied *after* the profile override so
    # it is always enforced regardless of the profile used.
    # Formula: hard_cap = equity × MAX_LOT_EQUITY_FRACTION / pip_value_per_lot
    # With default pip_value=10 and fraction=0.05: $10k equity → 5 lots max.
    _MAX_LOT_EQUITY_FRACTION = 0.05   # 5% of equity as max notional risk cap
    _DEFAULT_PIP_VALUE_PER_LOT = 10.0  # USD per pip per standard lot

    def apply(
        self,
        settings_dict: dict,
        balance: float,
        profile_override: str = "AUTO",
        equity: Optional[float] = None,
    ) -> dict:
        """
        Return a copy of *settings_dict* with risk parameters overridden
        according to the capital profile.

        In CUSTOM mode or when the user has set profile_override='CUSTOM',
        no changes are made except the hard cap on max_lot is still enforced
        when ``equity`` is provided.

        Parameters
        ----------
        settings_dict:
            Current risk settings dict (may be mutated into a copy).
        balance:
            Account balance used for profile selection.
        profile_override:
            Named profile or "AUTO".
        equity:
            Current account equity.  When provided, the hard lot cap
            ``equity × 5% / pip_value_per_lot`` is enforced.  Prevents
            compounding from accumulating dangerously large lot sizes.
        """
        name = profile_override.upper()
        if name == "CUSTOM":
            updated = dict(settings_dict)
            if equity is not None and equity > 0:
                updated = self._apply_hard_lot_cap(updated, equity)
            return updated

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
        if equity is not None and equity > 0:
            updated = self._apply_hard_lot_cap(updated, equity)
        logger.info(
            "CapitalManager: profile=%s balance=%.2f → lot_mode=%s lot_value=%.3f "
            "max_lot=%.2f dd_daily=%.1f%% dd_overall=%.1f%%",
            profile.profile, balance,
            profile.lot_mode, profile.lot_value, float(updated.get("max_lot", profile.max_lot)),
            profile.max_daily_dd_pct, profile.max_overall_dd_pct,
        )
        return updated

    def _apply_hard_lot_cap(self, settings: dict, equity: float) -> dict:
        """Clamp max_lot to the hard equity-based cap.

        Cap = equity × _MAX_LOT_EQUITY_FRACTION / pip_value_per_lot

        This prevents accumulated compounding gains from inflating lot sizes
        beyond what the account can sustain given realistic drawdown scenarios.
        """
        pip_value = float(
            settings.get("pip_value_per_lot") or self._DEFAULT_PIP_VALUE_PER_LOT
        )
        if pip_value <= 0:
            pip_value = self._DEFAULT_PIP_VALUE_PER_LOT
        hard_cap = (float(equity) * self._MAX_LOT_EQUITY_FRACTION) / pip_value
        hard_cap = max(settings.get("min_lot", 0.01), round(hard_cap, 2))
        current_max = float(settings.get("max_lot", hard_cap))
        if current_max > hard_cap:
            logger.warning(
                "CapitalManager: max_lot %.2f exceeds equity-based hard cap %.2f "
                "(equity=%.2f) — clamping",
                current_max, hard_cap, equity,
            )
            settings = dict(settings)
            settings["max_lot"] = hard_cap
        return settings

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
