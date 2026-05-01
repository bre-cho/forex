"""mt5_bridge — FastAPI HTTP bridge exposing MetaTrader 5 SDK over HTTP.

Runs on a Windows host (where the MetaTrader5 Python SDK is available) and
exposes a simple REST API consumed by MT5BridgeProvider running in a Linux
Docker container.

Deploy workflow:
  1. Copy this service onto your Windows MT5 host.
  2. Install dependencies: pip install -r requirements.txt
  3. Start: uvicorn mt5_bridge.main:app --host 0.0.0.0 --port 8181
  4. Point MT5BridgeProvider at http://<windows-host>:8181
"""

__version__ = "0.1.0"
