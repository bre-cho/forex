# Real-Time Events

## Architecture

Real-time updates flow through Redis pub/sub to WebSocket clients.

```
BotRuntime ──publish──► Redis channel ──subscribe──► WebSocket handler ──► Browser
```

## Channels

| Channel | Publisher | Subscriber |
|---------|-----------|------------|
| `signals:{bot_id}` | signal-service | ws.py BotWebSocket |
| `bot:{bot_id}` | apps/api bots router | ws.py BotWebSocket |
| `workspace:{id}:notifications` | apps/api | ws.py WorkspaceWebSocket |

## Event Shapes

See `packages/shared-schemas/contracts/events.json` for the full event registry.

Example signal event:
```json
{
  "event_id": "uuid",
  "event_type": "signal.generated",
  "bot_instance_id": "bot_001",
  "timestamp": "2024-01-01T00:00:00Z",
  "payload": {
    "signal_id": "uuid",
    "symbol": "EURUSD",
    "direction": "buy",
    "confidence": 0.82,
    "wave_state": "WAVE_3",
    "entry_price": 1.10234,
    "stop_loss": 1.09800,
    "take_profit": 1.11100
  }
}
```
