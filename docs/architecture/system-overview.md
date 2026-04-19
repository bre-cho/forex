# System Overview

## Architecture Summary

The Forex Trading Platform is a multi-tenant algorithmic trading system built as a Turborepo monorepo. It supports multiple users, each with their own isolated trading bots running against real or paper broker connections.

## High-Level Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         Clients                                 │
│  Browser (Next.js web)  │  Browser (admin)  │  Streamlit (legacy) │
└───────────┬─────────────────┬───────────────────┬───────────────┘
            │                 │                   │
            ▼                 ▼                   ▼
┌─────────────────────────────────────────────────────────────────┐
│                       Nginx Reverse Proxy                        │
└────────────────────────────┬────────────────────────────────────┘
                             │
            ┌────────────────▼────────────────┐
            │         FastAPI (apps/api)       │
            │   REST /v1/*  │  WS /ws/*        │
            │   Legacy /api/* (backward compat) │
            └────┬──────────────────────┬──────┘
                 │                      │
          ┌──────▼──────┐        ┌──────▼──────┐
          │  PostgreSQL  │        │    Redis     │
          │  (primary DB)│        │  (cache/pub) │
          └─────────────┘        └─────────────┘
                 │
    ┌────────────▼────────────────────────────┐
    │         RuntimeRegistry                  │
    │  ┌────────────┐  ┌────────────┐         │
    │  │ BotRuntime │  │ BotRuntime │  ...     │
    │  │  (bot_001) │  │  (bot_002) │         │
    │  └──────┬─────┘  └─────┬──────┘         │
    └─────────│───────────────│────────────────┘
              │               │
    ┌─────────▼───┐   ┌───────▼────┐
    │ cTrader API  │   │  MockData  │
    │  (live)      │   │  (paper)   │
    └─────────────┘   └────────────┘
```

## Services

| Service | Language | Role |
|---------|----------|------|
| `apps/api` | Python/FastAPI | REST + WebSocket API gateway |
| `apps/web` | TypeScript/Next.js | Trader dashboard |
| `apps/admin` | TypeScript/Next.js | Admin panel |
| `services/trading-core` | Python | Trading engines + BotRuntime |
| `services/execution-service` | Python | Broker provider abstraction |
| `services/analytics-service` | Python | Performance analytics |
| `services/signal-service` | Python | Signal generation + broadcasting |
| `services/notification-service` | Python | Email/Telegram/Discord/Webhook |
| `services/billing-service` | Python | Stripe billing + entitlements |

## Data Flow

1. Bot ticks every 5 seconds via `BotRuntime._run_loop()`
2. WaveDetector analyses OHLCV candles
3. SignalCoordinator generates trade signal
4. EntryLogic validates entry conditions
5. RiskManager checks position size limits
6. TradeManager submits order via ExecutionService
7. Trade recorded in PostgreSQL
8. Event published to Redis pub/sub
9. WebSocket clients receive real-time update
