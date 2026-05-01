"""MT5 Bridge — FastAPI HTTP server wrapping MetaTrader5 SDK.

Runs on a **Windows** host where the MetaTrader5 Python package is installed.
Linux containers consume it via MT5BridgeProvider.

Usage:
    uvicorn mt5_bridge.main:app --host 0.0.0.0 --port 8181

Security: bind to localhost or a private network interface only; do NOT
expose this service directly to the public internet.  Use an SSH tunnel or
VPN to reach it from your Docker host.

Environment variables:
    MT5_LOGIN      - MetaTrader 5 account login (integer)
    MT5_PASSWORD   - MetaTrader 5 account password
    MT5_SERVER     - Broker server address (e.g. "ICMarketsSC-Demo")
    MT5_SYMBOL     - Default symbol (default: "EURUSD")
    MT5_TIMEFRAME  - Default timeframe (default: "M5")
    MT5_LIVE       - "true" if connecting to a live account (default: "false")
    BRIDGE_API_KEY - Optional shared secret for basic auth (recommended)
"""
from __future__ import annotations

import asyncio
import logging
import os
from functools import lru_cache
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel

from .mt5_session import MT5Session, MT5SessionConfig

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(
    title="MT5 HTTP Bridge",
    version="0.1.0",
    description="HTTP bridge exposing MetaTrader 5 SDK for Linux Docker consumers.",
)


# ---------------------------------------------------------------------------
# Session singleton
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _get_session() -> MT5Session:
    cfg = MT5SessionConfig(
        login=int(os.environ["MT5_LOGIN"]),
        password=os.environ["MT5_PASSWORD"],
        server=os.environ["MT5_SERVER"],
        symbol=os.getenv("MT5_SYMBOL", "EURUSD"),
        timeframe=os.getenv("MT5_TIMEFRAME", "M5"),
        live=os.getenv("MT5_LIVE", "false").strip().lower() == "true",
    )
    return MT5Session(cfg)


# ---------------------------------------------------------------------------
# Simple API-key authentication (optional but strongly recommended)
# ---------------------------------------------------------------------------

_BRIDGE_API_KEY = os.getenv("BRIDGE_API_KEY", "").strip()


def _verify_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    if not _BRIDGE_API_KEY:
        return  # No key configured — open access (not recommended for production)
    if x_api_key != _BRIDGE_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid_api_key",
        )


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class PlaceOrderRequest(BaseModel):
    symbol: str
    side: str
    volume: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    comment: str = ""


class ClosePositionRequest(BaseModel):
    position_id: int


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------


@app.on_event("startup")
async def _startup() -> None:
    session = _get_session()
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, session.connect)
        logger.info("MT5 session connected on startup")
    except Exception as exc:
        logger.error("MT5 session connect failed on startup: %s", exc)
        # Do not raise — let the /connect endpoint retry.


@app.on_event("shutdown")
async def _shutdown() -> None:
    session = _get_session()
    session.disconnect()
    logger.info("MT5 session disconnected on shutdown")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.post("/connect", dependencies=[Depends(_verify_api_key)])
async def connect() -> Dict[str, Any]:
    session = _get_session()
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, session.connect)
    return {"ok": True, "connected": session.is_connected}


@app.post("/disconnect", dependencies=[Depends(_verify_api_key)])
async def disconnect() -> Dict[str, Any]:
    session = _get_session()
    session.disconnect()
    return {"ok": True, "connected": session.is_connected}


@app.get("/health")
async def health() -> Dict[str, Any]:
    session = _get_session()
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, session.health_check)


@app.get("/account", dependencies=[Depends(_verify_api_key)])
async def account() -> Dict[str, Any]:
    session = _get_session()
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, session.get_account_info)


@app.get("/candles", dependencies=[Depends(_verify_api_key)])
async def candles(
    symbol: str = "EURUSD",
    timeframe: str = "M5",
    limit: int = 200,
) -> List[Dict[str, Any]]:
    session = _get_session()
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: session.get_candles(symbol, timeframe, limit)
    )


@app.get("/quote", dependencies=[Depends(_verify_api_key)])
async def quote(symbol: str = "EURUSD") -> Optional[Dict[str, Any]]:
    session = _get_session()
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: session.get_quote(symbol))


@app.get("/instrument_spec", dependencies=[Depends(_verify_api_key)])
async def instrument_spec(symbol: str) -> Optional[Dict[str, Any]]:
    session = _get_session()
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: session.get_instrument_spec(symbol))


@app.get("/estimate_margin", dependencies=[Depends(_verify_api_key)])
async def estimate_margin_endpoint(
    symbol: str,
    side: str,
    volume: float,
    price: float,
) -> Dict[str, Any]:
    session = _get_session()
    loop = asyncio.get_event_loop()
    margin = await loop.run_in_executor(
        None, lambda: session.estimate_margin(symbol, side, volume, price)
    )
    return {"margin": margin}


@app.post("/order", dependencies=[Depends(_verify_api_key)])
async def place_order(req: PlaceOrderRequest) -> Dict[str, Any]:
    session = _get_session()
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: session.place_order(
            symbol=req.symbol,
            side=req.side,
            volume=req.volume,
            stop_loss=req.stop_loss,
            take_profit=req.take_profit,
            comment=req.comment,
        ),
    )


@app.post("/close_position", dependencies=[Depends(_verify_api_key)])
async def close_position(req: ClosePositionRequest) -> Dict[str, Any]:
    session = _get_session()
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: session.close_position(req.position_id)
    )


@app.get("/positions", dependencies=[Depends(_verify_api_key)])
async def positions() -> List[Dict[str, Any]]:
    session = _get_session()
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, session.get_positions)


@app.get("/history", dependencies=[Depends(_verify_api_key)])
async def history(limit: int = 100) -> List[Dict[str, Any]]:
    session = _get_session()
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: session.get_history(limit=limit))


@app.get("/order_by_client_id", dependencies=[Depends(_verify_api_key)])
async def order_by_client_id(client_order_id: str) -> Optional[Dict[str, Any]]:
    session = _get_session()
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: session.get_order_by_client_id(client_order_id)
    )


@app.get("/executions_by_client_id", dependencies=[Depends(_verify_api_key)])
async def executions_by_client_id(
    client_order_id: str,
) -> List[Dict[str, Any]]:
    session = _get_session()
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: session.get_executions_by_client_id(client_order_id)
    )


@app.get("/server_time", dependencies=[Depends(_verify_api_key)])
async def server_time() -> Dict[str, Any]:
    session = _get_session()
    loop = asyncio.get_event_loop()
    ts = await loop.run_in_executor(None, session.get_server_time)
    return {"server_time": ts}
