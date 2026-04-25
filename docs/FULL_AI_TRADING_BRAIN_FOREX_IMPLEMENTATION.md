# FULL AI TRADING BRAIN FOR FOREX-MAIN

## Goal
Apply the AI_SYSTEM_FULL thinking to `forex-main` and upgrade it from a normal trading bot into a controlled AI trading operating system.

This patch is designed for Forex, not BO. Forex requires position sizing, spread filtering, SL/TP, RR, drawdown control, open-position limits, broker connectivity checks, and outcome memory.

## Core Law

```text
VALID     -> ALLOW
INVALID   -> BLOCK
UNCERTAIN -> SKIP
DEFAULT   -> DENY
```

## New Brain Layer

```text
Market data
  -> Signal generation
  -> Governance preflight
  -> Decision Engine
  -> Risk-adjusted execution decision
  -> Trade outcome memory
  -> Policy evolution
  -> Safe policy update
```

## New Modules

```text
ai_trading_brain/
  decision_engine.py   # composite scoring: confidence + RR + spread + trend + session + volatility
  memory_engine.py     # append-only outcome memory
  evolution_engine.py  # safe policy mutation with bounded limits
  governance.py        # kill-switch, broker, drawdown, consecutive-loss guard
  brain_runtime.py     # facade for BotRuntime integration
```

## Integration Target

Best integration point in `forex-main`:

```text
services/trading-core/trading_core/runtime/bot_runtime.py
```

The current runtime already has:

```text
_fetch_market_data()
_analyse_market()
_generate_signal()
_manage_trades()
_persist_snapshot()
```

Patch only `_init_engines()` and `_manage_trades()`. Do not rewrite the whole runtime.

## Step 1 — Copy module

Copy this folder into:

```text
services/trading-core/trading_core/ai_trading_brain/
```

or install as a shared package and import from there.

Recommended repo path:

```text
services/trading-core/trading_core/ai_trading_brain/
```

## Step 2 — Initialize Brain in BotRuntime

In:

```text
services/trading-core/trading_core/runtime/bot_runtime.py
```

inside `_init_engines()`, add:

```python
from trading_core.ai_trading_brain import ForexBrainRuntime

self._ai_brain = ForexBrainRuntime(
    policy=self.ai_config.get("brain_policy", {}),
    governance_config=self.ai_config.get("brain_governance", {}),
)
```

Also add in `__init__` lazy fields:

```python
self._ai_brain = None
```

## Step 3 — Upgrade generated signal

Current `_generate_signal()` is minimal:

```python
return {"wave_state": str(getattr(wave, "main_wave", "")), "confidence": getattr(wave, "confidence", 0.0)}
```

Upgrade it to include Forex fields:

```python
last_close = float(df["close"].iloc[-1])
atr_pips = float(signal.get("atr_pips", 0.0)) if isinstance(signal, dict) else 0.0

return {
    "symbol": getattr(self.broker_provider, "symbol", "EURUSD"),
    "direction": "BUY" if getattr(wave, "trend", "").upper() == "UP" else "SELL",
    "wave_state": str(getattr(wave, "main_wave", "")),
    "confidence": float(getattr(wave, "confidence", 0.0)),
    "trend_strength": float(getattr(wave, "trend_strength", getattr(wave, "confidence", 0.0))),
    "spread_pips": float(getattr(self.broker_provider, "spread_pips", 0.0)),
    "atr_pips": atr_pips,
    "rr": 1.8,
    "last_close": last_close,
}
```

If wave detector does not expose trend, keep direction as `HOLD` until entry logic confirms side.

## Step 4 — Patch `_manage_trades()`

Replace placeholder logic:

```python
async def _manage_trades(self, signal: Dict[str, Any]) -> None:
    self.state.metadata["last_signal"] = signal
```

with guarded decision logic:

```python
async def _manage_trades(self, signal: Dict[str, Any]) -> None:
    self.state.metadata["last_signal"] = signal

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
        return

    # REDUCE lowers lot. ALLOW uses normal lot multiplier.
    lot_multiplier = decision.lot_multiplier

    # Wire this into your existing TradeManager / ExecutionService call.
    # Example:
    # await self._trade_manager.open_position(
    #     signal=signal,
    #     lot_multiplier=lot_multiplier,
    #     sl_pips=decision.suggested_sl_pips,
    #     tp_pips=decision.suggested_tp_pips,
    # )
```

## Step 5 — Record trade outcome

When a trade closes, call:

```python
from trading_core.ai_trading_brain import TradeOutcome

self._ai_brain.record_outcome(TradeOutcome(
    trade_id=str(trade.id),
    symbol=trade.symbol,
    direction=trade.direction,
    opened_at=trade.opened_at.timestamp(),
    closed_at=trade.closed_at.timestamp(),
    entry_price=float(trade.entry_price),
    exit_price=float(trade.exit_price),
    pnl=float(trade.pnl),
    pnl_pips=float(trade.pnl_pips),
    decision_score=float(trade.metadata.get("decision_score", 0.0)),
    decision_reason=str(trade.metadata.get("decision_reason", "")),
    policy_snapshot=trade.metadata.get("policy_snapshot", {}),
))
```

Recommended target: `TradeManager` close-position flow or execution-service fill handler.

## Step 6 — Run policy evolution safely

Call evolution periodically, not every tick:

```python
payload = self._ai_brain.evolve_policy()
self.state.metadata["last_policy_evolution"] = payload
```

Recommended cadence:

```text
Every 30 closed trades
OR once per day after market close
OR manually through admin endpoint
```

Never evolve during an open position unless you snapshot the old policy on that trade.

## Verify

```bash
python -m py_compile services/trading-core/trading_core/ai_trading_brain/*.py
pytest services/trading-core/tests -q
```

Smoke test:

```bash
python tests/test_forex_ai_trading_brain_smoke.py
```

Expected:

```text
ALLOW or REDUCE for strong signal
SKIP for weak signal
BLOCK for bad broker/drawdown/spread
policy evolution waits until enough samples
```

## Next strongest patch

After this patch, the next strongest upgrade is:

```text
FOREX BRAIN API + DASHBOARD PATCH
```

Add endpoints:

```text
GET  /v1/brain/status
POST /v1/brain/decide
POST /v1/brain/outcome
POST /v1/brain/evolve
POST /v1/brain/kill-switch
```

That makes the AI brain observable and controllable in production.
