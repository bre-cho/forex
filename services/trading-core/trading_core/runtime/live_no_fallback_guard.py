from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LiveNoFallbackResult:
    ok: bool
    reason: str = "ok"


class LiveNoFallbackGuard:
    """Fail-closed guard that rejects any fallback/stub markers in live mode."""

    _BAD_PROVIDER_MODES = {"stub", "paper", "degraded", "unavailable"}
    _FALLBACK_MARKERS = {
        "instrument_spec_source": {"fallback", "default", "synthetic"},
        "quote_source": {"fallback", "default", "synthetic"},
        "equity_source": {"fallback", "default", "synthetic", "cached"},
    }

    @classmethod
    def evaluate(cls, context: dict[str, Any]) -> LiveNoFallbackResult:
        runtime_mode = str(context.get("runtime_mode", "") or "").lower()
        if runtime_mode != "live":
            return LiveNoFallbackResult(ok=True)

        provider_mode = str(context.get("provider_mode", "") or "").lower()
        if provider_mode in cls._BAD_PROVIDER_MODES:
            return LiveNoFallbackResult(ok=False, reason="provider_mode_not_live_capable")

        for key, bad_values in cls._FALLBACK_MARKERS.items():
            val = str(context.get(key, "") or "").lower()
            if val and val in bad_values:
                return LiveNoFallbackResult(ok=False, reason=f"{key}_fallback_forbidden_in_live")

        # Explicit fallback booleans set by runtime/risk/execution context builders.
        if bool(context.get("instrument_spec_is_fallback", False)):
            return LiveNoFallbackResult(ok=False, reason="instrument_spec_fallback_forbidden_in_live")
        if bool(context.get("quote_is_fallback", False)):
            return LiveNoFallbackResult(ok=False, reason="quote_fallback_forbidden_in_live")
        if bool(context.get("equity_is_fallback", False)):
            return LiveNoFallbackResult(ok=False, reason="equity_fallback_forbidden_in_live")

        return LiveNoFallbackResult(ok=True)
