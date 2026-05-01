"""Tests for CapitalManager hard lot cap (P1.4)."""
from __future__ import annotations

import pytest

from trading_core.engines.capital_manager import CapitalManager


class TestCapitalManagerHardCap:
    def setup_method(self):
        self.cm = CapitalManager()

    def test_hard_cap_clamps_max_lot_when_equity_small(self):
        """With $1000 equity, hard cap = 1000 × 0.05 / 10 = 5 lots."""
        settings = {"lot_mode": "DYNAMIC_PERCENT", "lot_value": 1.0, "min_lot": 0.01, "max_lot": 100.0}
        result = self.cm.apply(settings, balance=1000.0, profile_override="CUSTOM", equity=1000.0)
        # 1000 * 0.05 / 10 = 5.0
        assert result["max_lot"] == pytest.approx(5.0, rel=0.01)

    def test_hard_cap_does_not_clamp_when_max_lot_already_below(self):
        """If user-configured max_lot is below the cap, it is preserved."""
        settings = {"lot_mode": "STATIC", "lot_value": 0.01, "min_lot": 0.01, "max_lot": 0.5}
        result = self.cm.apply(settings, balance=10000.0, profile_override="CUSTOM", equity=10000.0)
        # 10000 * 0.05 / 10 = 50 lots — far above 0.5, so unchanged
        assert result["max_lot"] == pytest.approx(0.5)

    def test_hard_cap_respects_custom_pip_value(self):
        """Custom pip_value_per_lot is used in the hard cap calculation."""
        settings = {
            "lot_mode": "DYNAMIC_PERCENT",
            "lot_value": 1.0,
            "min_lot": 0.01,
            "max_lot": 50.0,
            "pip_value_per_lot": 5.0,  # cheaper pip value → higher cap
        }
        result = self.cm.apply(settings, balance=2000.0, profile_override="CUSTOM", equity=2000.0)
        # 2000 * 0.05 / 5 = 20.0 lots — cap is 20, max_lot was 50 → clamp to 20
        assert result["max_lot"] == pytest.approx(20.0)

    def test_no_equity_no_cap(self):
        """When equity is not provided, no cap is applied."""
        settings = {"lot_mode": "STATIC", "lot_value": 0.01, "min_lot": 0.01, "max_lot": 200.0}
        result = self.cm.apply(settings, balance=5000.0, profile_override="CUSTOM")
        assert result["max_lot"] == pytest.approx(200.0)

    def test_auto_profile_applies_cap(self):
        """AUTO profile also enforces the hard cap."""
        # SMALL profile sets max_lot=0.5; hard cap with $2000 equity = 10 lots
        # → cap (10) > profile max (0.5) so profile max is preserved
        settings: dict = {}
        result = self.cm.apply(settings, balance=2000.0, profile_override="AUTO", equity=2000.0)
        # profile max_lot for SMALL = 0.5; hard cap = 10 → no clamping needed
        assert result["max_lot"] == pytest.approx(0.5)

    def test_auto_profile_clamps_profile_max_lot_when_equity_very_large(self):
        """Hard cap clamps even profile-suggested max_lot for huge equity."""
        # LARGE profile sets max_lot=10; hard cap with $100 equity = 0.5 lots
        # → cap (0.5) < profile max (10) → clamped to 0.5
        settings: dict = {}
        result = self.cm.apply(settings, balance=100.0, profile_override="LARGE", equity=100.0)
        # 100 * 0.05 / 10 = 0.5
        assert result["max_lot"] == pytest.approx(0.5)

    def test_hard_cap_not_below_min_lot(self):
        """Hard cap floor is always at least min_lot."""
        settings = {"min_lot": 0.1, "max_lot": 500.0}
        # 1 * 0.05 / 10 = 0.005 → but min_lot is 0.1 → floor to 0.1
        result = self.cm.apply(settings, balance=1.0, profile_override="CUSTOM", equity=1.0)
        assert result["max_lot"] >= 0.1
