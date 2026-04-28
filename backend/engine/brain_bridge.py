from __future__ import annotations

from typing import Any, Dict, List

try:
    from ai_trading_brain.brain_contracts import BrainInput
    from ai_trading_brain.brain_runtime import ForexBrainRuntime
    from ai_trading_brain.engine_registry import TradingEngineRegistry
except Exception:  # noqa: BLE001
    BrainInput = None  # type: ignore
    ForexBrainRuntime = None  # type: ignore
    TradingEngineRegistry = None  # type: ignore


class TradingBrainBridge:
    """Adapter that wires backend/core engines into the AI Trading Brain."""

    def __init__(self, app_state: Any) -> None:
        self.app_state = app_state
        self.available = ForexBrainRuntime is not None
        self.runtime = self._build_runtime() if self.available else None

    def _build_runtime(self) -> Any:
        registry = TradingEngineRegistry()
        # Register real engines when present. Missing engines are not fatal; health shows fallback.
        for name in [
            "adaptive_controller", "auto_pilot", "autonomous_enterprise_engine", "candle_library",
            "capital_manager", "causal_strategy_engine", "decision_engine", "entry_logic",
            "game_theory_engine", "llm_orchestrator", "meta_learning_engine", "performance_tracker",
            "retracement_engine", "risk_manager", "self_play_evolutionary_engine", "session_manager",
            "signal_coordinator", "sovereign_oversight_engine", "synthetic_warmup_engine",
            "trade_manager", "utility_optimization_engine", "wave_detector",
        ]:
            attr = self._map_attr(name)
            registry.register(name, getattr(self.app_state, attr, None), critical=name in {"risk_manager", "trade_manager", "signal_coordinator"})
        runtime = ForexBrainRuntime(registry=registry)
        return runtime

    def _map_attr(self, engine_name: str) -> str:
        return {
            "llm_orchestrator": "llm",
            "self_play_evolutionary_engine": "evolutionary_engine",
            "synthetic_warmup_engine": "warmup_pipeline",
        }.get(engine_name, engine_name)

    def health(self) -> Dict[str, Any]:
        if not self.available or self.runtime is None:
            return {"ok": True, "mode": "stub", "reason": "ai_trading_brain package unavailable"}
        return {"ok": True, **self.runtime.health()}

    def preview_cycle(self, *, symbol: str, broker: str, signal: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        if not self.available or self.runtime is None or BrainInput is None:
            return {"action": "SKIP", "reason": "brain_stub_unavailable", "mode": "stub"}
        item = BrainInput(
            symbol=symbol,
            timeframe=str(context.get("timeframe", "M5")),
            broker=broker,
            market={
                "market_data_ok": context.get("market_data_ok", True),
                "broker_connected": context.get("broker_connected", broker != "stub"),
                "spread_pips": context.get("spread_pips", 0.0),
                "atr_pips": context.get("atr_pips", 0.0),
                "session_score": context.get("session_score", 0.5),
                "volatility_score": context.get("volatility_score", 0.5),
            },
            account={
                "equity": context.get("account_equity", 0.0),
                "daily_loss_pct": context.get("daily_loss_pct", 0.0),
                "consecutive_losses": context.get("consecutive_losses", 0),
            },
            positions=context.get("positions", []),
            signals=[signal],
            settings={"risk_pct": context.get("risk_pct", 0.5)},
            telemetry=context,
        )
        return self.runtime.run_cycle(item).to_dict()
