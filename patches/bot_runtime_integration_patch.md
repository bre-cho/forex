# BotRuntime Integration Patch

## File

```text
services/trading-core/trading_core/runtime/bot_runtime.py
```

## Patch 1 — Add lazy field

Inside `__init__` after existing engine fields:

```python
self._ai_brain = None
```

## Patch 2 — Initialize AI Brain

Inside `_init_engines()` after `self._auto_pilot = AutoPilot()`:

```python
from trading_core.ai_trading_brain import ForexBrainRuntime

self._ai_brain = ForexBrainRuntime(
    policy=self.ai_config.get("brain_policy", {}),
    governance_config=self.ai_config.get("brain_governance", {}),
)
```

## Patch 3 — Replace `_manage_trades()` placeholder

```python
async def _manage_trades(self, signal: Dict[str, Any]) -> None:
    self.state.metadata["last_signal"] = signal

    if self._ai_brain is None:
        logger.warning("AI brain not initialized for %s", self.bot_instance_id)
        return

    context = {
        "symbol": signal.get("symbol", getattr(self.broker_provider, "symbol", "EURUSD")),
        "broker_connected": bool(getattr(self.broker_provider, "is_connected", False)),
        "market_data_ok": True,
        "daily_loss_pct": float(self.state.metadata.get("daily_loss_pct", 0.0)),
        "consecutive_losses": int(self.state.metadata.get("consecutive_losses", 0)),
        "open_positions": int(self.state.metadata.get("open_positions", 0)),
        "spread_pips": float(signal.get("spread_pips", 0.0)),
        "atr_pips": float(signal.get("atr_pips", 0.0)),
        "rr": float(signal.get("rr", 0.0)),
        "trend_strength": float(signal.get("trend_strength", 0.0)),
        "account_equity": float(self.state.metadata.get("account_equity", 0.0)),
    }

    decision = self._ai_brain.decide(signal, context)
    self.state.metadata["last_ai_decision"] = decision.__dict__

    if decision.action in {"BLOCK", "SKIP"}:
        logger.info("AI brain %s trade: %s", decision.action, decision.reason)
        return

    # TODO: connect to existing trade manager / execution service.
    # Use decision.lot_multiplier, decision.suggested_sl_pips, decision.suggested_tp_pips.
```
