# broker-sdk

Broker SDK abstractions for the Forex platform.

This package provides common interfaces and utilities for integrating
with trading brokers. Concrete implementations live in `services/execution-service`.

## Supported Brokers

| Broker | Status | Package |
|--------|--------|---------|
| cTrader | ✅ Implemented | `execution_service.providers.ctrader` |
| Paper Trading | ✅ Implemented | `execution_service.providers.paper` |
| MetaTrader 5 | 🚧 Stub | `execution_service.providers.mt5` |
| Bybit | 🚧 Stub | `execution_service.providers.bybit` |

## Usage

```python
from execution_service.providers import get_provider

provider = get_provider("paper", symbol="EURUSD", initial_balance=10000)
await provider.connect()
account = await provider.get_account_info()
print(account.balance)
```

## Adding a New Broker

1. Create `execution_service/providers/mybroker.py`
2. Extend `BrokerProvider` from `execution_service.providers.base`
3. Implement all abstract methods
4. Register in `execution_service/providers/__init__.py`
