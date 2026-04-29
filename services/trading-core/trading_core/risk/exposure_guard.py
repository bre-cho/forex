from __future__ import annotations


def exposure_ratio(current_notional: float, new_notional: float, equity: float) -> float:
    eq = max(1e-12, float(equity or 0.0))
    return max(0.0, float(current_notional or 0.0) + float(new_notional or 0.0)) / eq
