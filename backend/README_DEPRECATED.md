# ⚠️ LEGACY BACKEND — DEPRECATED

This directory contains the **legacy monolithic backend** from the pre-monorepo era.

## Status: ARCHIVED — DO NOT USE IN PRODUCTION

All production code has been migrated to:

| Legacy module | New location |
|---|---|
| `backend/engine/ctrader_provider.py` | `services/execution-service/execution_service/providers/ctrader.py` |
| `backend/engine/risk_manager.py` | `services/trading-core/trading_core/risk/` |
| `backend/engine/trade_manager.py` | `services/trading-core/trading_core/runtime/bot_runtime.py` |
| `backend/engine/decision_engine.py` | `services/trading-core/trading_core/engines/decision_engine.py` |
| `backend/engine/data_provider.py` | `services/execution-service/execution_service/providers/` |
| `backend/engine/signal_coordinator.py` | `services/trading-core/trading_core/engines/signal_coordinator.py` |

## Production boundaries

- **Production Docker images** (`apps/api/Dockerfile`) do NOT copy this directory.
- **CI** enforces that `apps/` and `services/` do not import from `backend.*`.
- Any import of `backend.` from the new stack will **fail CI**.

## Safe to delete?

Yes, after verifying:
1. No active `services/` or `apps/` code imports from `backend/`
2. All live bot strategies have been migrated and tested with new stack
