from ai_trading_brain import ForexBrainRuntime


def test_strong_signal_allows_or_reduces():
    brain = ForexBrainRuntime()
    result = brain.decide(
        {"symbol": "EURUSD", "direction": "BUY", "confidence": 0.85, "spread_pips": 0.8, "rr": 2.0, "trend_strength": 0.8},
        {"broker_connected": True, "market_data_ok": True, "open_positions": 0},
    )
    assert result.action in {"ALLOW", "REDUCE"}


def test_weak_signal_skips():
    brain = ForexBrainRuntime()
    result = brain.decide(
        {"symbol": "EURUSD", "direction": "BUY", "confidence": 0.3, "spread_pips": 0.8, "rr": 2.0},
        {"broker_connected": True, "market_data_ok": True, "open_positions": 0},
    )
    assert result.action == "SKIP"


def test_broker_disconnected_blocks():
    brain = ForexBrainRuntime()
    result = brain.decide(
        {"symbol": "EURUSD", "direction": "BUY", "confidence": 0.9, "spread_pips": 0.8, "rr": 2.0},
        {"broker_connected": False, "market_data_ok": True, "open_positions": 0},
    )
    assert result.action == "BLOCK"
