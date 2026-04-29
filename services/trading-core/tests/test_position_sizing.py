from __future__ import annotations

from trading_core.risk import PositionSizingInput, calculate_position_size


def test_position_sizing_calculates_reasonable_lot() -> None:
    result = calculate_position_size(
        PositionSizingInput(
            equity=10000.0,
            risk_pct=1.0,
            entry_price=1.1000,
            stop_loss=1.0950,
            pip_size=0.0001,
            pip_value_per_lot=10.0,
            min_lot=0.01,
            max_lot=10.0,
            lot_step=0.01,
        )
    )

    assert round(result.risk_amount, 2) == 100.00
    assert round(result.stop_distance_pips, 2) == 50.00
    assert round(result.lot, 2) == 0.20


def test_position_sizing_zero_risk_returns_zero_lot() -> None:
    result = calculate_position_size(
        PositionSizingInput(
            equity=10000.0,
            risk_pct=0.0,
            entry_price=1.1000,
            stop_loss=1.0950,
            pip_size=0.0001,
            pip_value_per_lot=10.0,
        )
    )
    assert result.lot == 0.0
