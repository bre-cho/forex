from ai_trading_brain.brain_contracts import BrainInput
from ai_trading_brain.brain_runtime import ForexBrainRuntime


def test_closed_loop_cycle_allows_good_signal():
    runtime = ForexBrainRuntime(governance_config={"require_broker_connected": False})
    result = runtime.run_cycle(BrainInput(
        symbol="EURUSD",
        broker="stub",
        market={"market_data_ok": True, "spread_pips": 1.0, "atr_pips": 10.0, "session_score": 0.8, "volatility_score": 0.7},
        account={"equity": 10000, "daily_loss_pct": 0, "consecutive_losses": 0},
        signals=[{"symbol": "EURUSD", "direction": "BUY", "confidence": 0.82, "rr": 2.0, "trend_strength": 0.8}],
        settings={"risk_pct": 0.5},
    ))
    assert result.action.value in {"ALLOW", "REDUCE"}
    assert result.execution_intent is not None
    assert result.stage_decisions[-1].stage.value == "MEMORY_LEARNING"


def test_closed_loop_blocks_bad_spread():
    runtime = ForexBrainRuntime(governance_config={"require_broker_connected": False})
    result = runtime.run_cycle(BrainInput(
        symbol="EURUSD",
        broker="stub",
        market={"market_data_ok": True, "spread_pips": 9.0, "atr_pips": 10.0},
        account={"equity": 10000},
        signals=[{"symbol": "EURUSD", "direction": "BUY", "confidence": 0.9, "rr": 2.0, "trend_strength": 0.8}],
    ))
    assert result.action.value == "BLOCK"
    assert result.execution_intent is None
