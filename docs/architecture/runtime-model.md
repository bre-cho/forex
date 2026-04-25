# Runtime Model

## Problem: Global AppState Singleton

The original `backend/main.py` used a global `AppState` singleton to hold all engine state. This made multi-user/multi-bot operation impossible — all users shared the same engine instances.

## Solution: BotRuntime + RuntimeRegistry

### BotRuntime

Each active bot instance gets its own `BotRuntime`. It holds:
- All engine instances (WaveDetector, SignalCoordinator, RiskManager, etc.)
- Its own broker provider connection
- Its own `RuntimeState` (status, balance, equity, PnL)
- Its own async run loop

```python
runtime = BotRuntime(
    bot_instance_id="bot_001",
    strategy_config={...},
    broker_provider=paper_provider,
    risk_config={...},
)
await runtime.start()
snapshot = await runtime.get_snapshot()
await runtime.stop()
```

### RuntimeRegistry

The `RuntimeRegistry` manages all active `BotRuntime` instances in the process:

```python
registry = RuntimeRegistry()
registry.create("bot_001", config, provider, risk)
await registry.start("bot_001")
await registry.get_snapshot("bot_001")
await registry.stop_all()
```

### Lifecycle

```
BotRuntime.start()
  → _init_engines()       # lazy-init all engine objects
  → state = RUNNING
  → asyncio.create_task(_run_loop())

_run_loop():
  while RUNNING or PAUSED:
    if RUNNING: await tick()
    await asyncio.sleep(tick_interval)

tick():
  → broker_provider.advance()
  → df = broker_provider.get_candles()
  → wave = wave_detector.analyse(df)
  → ... (signal, risk, entry, trade management)
```

## Migration from AppState

The original `AppState` singleton in `backend/main.py` is preserved for backward compatibility. The new `BotRuntime`/`RuntimeRegistry` pattern lives in `apps/api` and `services/trading-core`.

New bots use the registry; existing code continues using AppState until fully migrated.
