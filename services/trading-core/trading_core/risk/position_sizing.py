from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PositionSizingInput:
    equity: float
    risk_pct: float
    entry_price: float
    stop_loss: float
    pip_size: float
    pip_value_per_lot: float
    min_lot: float = 0.01
    max_lot: float = 100.0
    lot_step: float = 0.01


@dataclass(frozen=True)
class PositionSizingResult:
    lot: float
    risk_amount: float
    stop_distance_pips: float


def _round_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    return round(round(value / step) * step, 8)


def calculate_position_size(inp: PositionSizingInput) -> PositionSizingResult:
    equity = max(0.0, float(inp.equity or 0.0))
    risk_pct = max(0.0, float(inp.risk_pct or 0.0))
    entry = float(inp.entry_price or 0.0)
    sl = float(inp.stop_loss or 0.0)
    pip_size = max(1e-12, float(inp.pip_size or 0.0))
    pip_value = max(1e-12, float(inp.pip_value_per_lot or 0.0))

    risk_amount = equity * (risk_pct / 100.0)
    stop_distance = abs(entry - sl)
    stop_distance_pips = stop_distance / pip_size

    if risk_amount <= 0 or stop_distance_pips <= 0:
        return PositionSizingResult(lot=0.0, risk_amount=risk_amount, stop_distance_pips=stop_distance_pips)

    raw_lot = risk_amount / (stop_distance_pips * pip_value)
    lot = _round_step(raw_lot, max(1e-12, float(inp.lot_step or 0.01)))
    lot = max(float(inp.min_lot or 0.01), min(float(inp.max_lot or 100.0), lot))

    return PositionSizingResult(lot=lot, risk_amount=risk_amount, stop_distance_pips=stop_distance_pips)
