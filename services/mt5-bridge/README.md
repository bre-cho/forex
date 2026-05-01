# MT5 Bridge — README

## Overview

The MT5 Bridge is a lightweight FastAPI HTTP server that wraps the MetaTrader 5
Python SDK. Since the `MetaTrader5` Python package is a Windows-only DLL, it
cannot run inside a Linux Docker container.

**Architecture:**

```
Linux Docker Host
├── forex-api (container)
├── trading-core (container)
└── ...
        │
        │ HTTP (private network / VPN)
        │
Windows Host
└── mt5-bridge (runs on Windows with MT5 installed)
```

`MT5BridgeProvider` (in `services/execution-service/`) is the Linux-side HTTP
client that speaks to this bridge.

## Setup (Windows Host)

### Prerequisites
1. MetaTrader 5 installed and a terminal account configured
2. Python 3.9+ for Windows
3. MetaTrader5 Python package: `pip install MetaTrader5`

### Installation

```powershell
# Clone or copy the services/mt5-bridge/ directory to your Windows host
cd services/mt5-bridge

# Install dependencies
pip install -r requirements.txt

# Configure environment variables
$env:MT5_LOGIN = "12345678"
$env:MT5_PASSWORD = "your_mt5_password"
$env:MT5_SERVER = "ICMarketsSC-Demo"
$env:MT5_SYMBOL = "EURUSD"
$env:MT5_TIMEFRAME = "M5"
$env:MT5_LIVE = "false"          # Set to "true" for live accounts
$env:BRIDGE_API_KEY = "your_secret_api_key"

# Start the server
uvicorn mt5_bridge.main:app --host 0.0.0.0 --port 8181
```

### Windows Service (recommended for production)

```powershell
# Install as a Windows service using nssm (https://nssm.cc/)
nssm install MT5Bridge "C:\Python311\python.exe" `
  "-m uvicorn mt5_bridge.main:app --host 0.0.0.0 --port 8181"
nssm set MT5Bridge AppDirectory "C:\mt5-bridge"
nssm set MT5Bridge AppEnvironmentExtra MT5_LOGIN=12345678 ...
nssm start MT5Bridge
```

## Connecting from Linux

Configure `MT5BridgeProvider` in your bot's broker connection settings:

```python
from execution_service.providers import MT5BridgeProvider

provider = MT5BridgeProvider(
    bridge_url="http://192.168.1.100:8181",  # Windows host IP
    api_key="your_secret_api_key",
    symbol="EURUSD",
    timeframe="M5",
    mode="demo",
)
```

Or use `provider_type="mt5_bridge"` in the bot configuration.

## Security

- **Do NOT expose port 8181 to the public internet.**
- Use an SSH tunnel or a private VLAN/VPN between your Windows host and Docker host.
- Always set `BRIDGE_API_KEY` to a random secret of at least 32 characters.
- Consider mTLS for the bridge-to-container communication in high-security deployments.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/connect` | Connect to MT5 terminal |
| POST | `/disconnect` | Disconnect from MT5 terminal |
| GET | `/health` | Health check (no auth required) |
| GET | `/account` | Account info (balance, equity, margin) |
| GET | `/candles` | Historical candle bars |
| GET | `/quote` | Current bid/ask quote |
| GET | `/instrument_spec` | Instrument specification |
| GET | `/estimate_margin` | Margin estimation |
| POST | `/order` | Place market order |
| POST | `/close_position` | Close a position by ID |
| GET | `/positions` | List open positions |
| GET | `/history` | Trade history |
| GET | `/order_by_client_id` | Order lookup by comment/client ID |
| GET | `/executions_by_client_id` | Execution history by client ID |
| GET | `/server_time` | MT5 server time |

All endpoints except `/health` require `x-api-key` header when `BRIDGE_API_KEY` is set.
