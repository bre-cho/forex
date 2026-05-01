"""Tests for _advanced_guard.py — ENABLE_ADVANCED_ENGINES feature flag."""
from __future__ import annotations

import os
import warnings

import pytest


def _reload_guard(monkeypatch, app_env: str, enable_advanced: str):
    """Reload the guard module with patched env vars."""
    import importlib
    import trading_core.engines._advanced_guard as mod

    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("ENABLE_ADVANCED_ENGINES", enable_advanced)
    importlib.reload(mod)
    return mod


class TestRequireAdvancedEngines:
    def test_blocks_in_production_when_flag_off(self, monkeypatch):
        mod = _reload_guard(monkeypatch, "production", "false")
        with pytest.raises(RuntimeError, match="EvolutionaryEngine"):
            mod.require_advanced_engines("EvolutionaryEngine")

    def test_blocks_in_staging_when_flag_off(self, monkeypatch):
        mod = _reload_guard(monkeypatch, "staging", "false")
        with pytest.raises(RuntimeError, match="GameTheoryEngine"):
            mod.require_advanced_engines("GameTheoryEngine")

    def test_allows_in_production_when_flag_on(self, monkeypatch):
        mod = _reload_guard(monkeypatch, "production", "true")
        # Should not raise
        mod.require_advanced_engines("MetaLearningEngine")

    def test_warns_in_development_when_flag_off(self, monkeypatch):
        mod = _reload_guard(monkeypatch, "development", "false")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            mod.require_advanced_engines("SovereignOversightEngine")
        assert any(issubclass(w.category, RuntimeWarning) for w in caught)

    def test_no_warning_in_development_when_flag_on(self, monkeypatch):
        mod = _reload_guard(monkeypatch, "development", "true")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            mod.require_advanced_engines("CausalStrategyEngine")
        runtime_warnings = [w for w in caught if issubclass(w.category, RuntimeWarning)]
        assert len(runtime_warnings) == 0

    def test_warns_in_test_env_when_flag_off(self, monkeypatch):
        mod = _reload_guard(monkeypatch, "test", "false")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            mod.require_advanced_engines("UtilityOptimizationEngine")
        assert any(issubclass(w.category, RuntimeWarning) for w in caught)
