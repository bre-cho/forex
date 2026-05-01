"""trading_core.data.tick_stream — Real-time market data streaming interface.

Provides a unified ITickStream protocol for live tick/quote data across
all broker providers.  BotRuntime uses this for low-latency market data
instead of the poll-based get_candles() approach.

Usage example:
    from trading_core.data.tick_stream import BybitTickStream

    stream = BybitTickStream(session=bybit_session, symbol="BTCUSDT")
    async with stream:
        async for tick in stream:
            print(tick.bid, tick.ask, tick.timestamp)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TickEvent:
    """A single real-time price tick."""

    symbol: str
    bid: float
    ask: float
    timestamp: float          # Unix epoch seconds (UTC)
    spread_pips: float = 0.0
    last_price: float = 0.0   # Mid/last trade price where applicable
    quote_id: str = ""
    source: str = ""          # Provider name tag

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0 if self.ask > 0 else self.bid


@dataclass
class StreamStats:
    """Diagnostic counters for a running tick stream."""

    ticks_received: int = 0
    last_tick_ts: float = 0.0
    errors: int = 0
    reconnects: int = 0

    @property
    def age_seconds(self) -> float:
        return time.time() - self.last_tick_ts if self.last_tick_ts > 0 else float("inf")


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------


class ITickStream(ABC):
    """Abstract real-time tick stream.

    Concrete implementations (CTraderTickStream, BybitTickStream,
    MT5BridgeTickStream) must implement ``_connect()``, ``_disconnect()``,
    and ``__aiter__``.

    Context-manager usage (preferred):
        async with stream:
            async for tick in stream:
                process(tick)
    """

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.stats = StreamStats()
        self._running = False

    @abstractmethod
    async def _connect(self) -> None:
        """Establish the underlying streaming connection."""

    @abstractmethod
    async def _disconnect(self) -> None:
        """Close the underlying streaming connection."""

    @abstractmethod
    def __aiter__(self) -> AsyncIterator[TickEvent]:
        """Yield TickEvent objects as they arrive."""

    async def __aenter__(self) -> "ITickStream":
        await self._connect()
        self._running = True
        return self

    async def __aexit__(self, *_: Any) -> None:
        self._running = False
        await self._disconnect()

    def _record_tick(self) -> None:
        self.stats.ticks_received += 1
        self.stats.last_tick_ts = time.time()


# ---------------------------------------------------------------------------
# Bybit WebSocket tick stream (V5 API)
# ---------------------------------------------------------------------------


class BybitTickStream(ITickStream):
    """Real-time tick stream via Bybit V5 WebSocket.

    Requires the ``websockets`` package: pip install websockets

    The Bybit V5 public WebSocket endpoint streams orderbook-1 data which
    gives us best bid/ask in real-time.
    """

    _MAINNET_WS = "wss://stream.bybit.com/v5/public/linear"
    _TESTNET_WS = "wss://stream-testnet.bybit.com/v5/public/linear"

    def __init__(
        self,
        symbol: str,
        testnet: bool = False,
        pip_size: float = 0.01,
        reconnect_delay: float = 5.0,
    ) -> None:
        super().__init__(symbol)
        self._testnet = testnet
        self._pip_size = max(1e-12, float(pip_size))
        self._reconnect_delay = float(reconnect_delay)
        self._ws = None
        self._queue: asyncio.Queue[TickEvent] = asyncio.Queue(maxsize=1000)

    async def _connect(self) -> None:
        try:
            import websockets  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "BybitTickStream requires the websockets package. "
                "Install: pip install websockets"
            ) from exc
        ws_url = self._TESTNET_WS if self._testnet else self._MAINNET_WS
        self._ws = await websockets.connect(ws_url, ping_interval=20, ping_timeout=10)
        # Subscribe to orderbook (depth=1 gives best bid/ask)
        sub_msg = json.dumps(
            {"op": "subscribe", "args": [f"orderbook.1.{self.symbol}"]}
        )
        await self._ws.send(sub_msg)
        # Start background reader
        asyncio.ensure_future(self._reader_loop())
        logger.info("BybitTickStream connected: %s testnet=%s", self.symbol, self._testnet)

    async def _disconnect(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        logger.info("BybitTickStream disconnected: %s", self.symbol)

    async def _reader_loop(self) -> None:
        try:
            import websockets

            _reconnect_attempt = 0
            _max_backoff = 30.0

            while self._running and self._ws is not None:
                try:
                    raw = await asyncio.wait_for(self._ws.recv(), timeout=30.0)
                    msg = json.loads(raw)
                    tick = self._parse_message(msg)
                    if tick is not None:
                        await self._queue.put(tick)
                        self._record_tick()
                    # Reset backoff counter on successful message.
                    _reconnect_attempt = 0
                except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
                    if self._running:
                        # Exponential backoff: 1 s → 2 s → 4 s … capped at 30 s.
                        backoff = min(_max_backoff, float(2 ** _reconnect_attempt))
                        _reconnect_attempt += 1
                        self.stats.reconnects += 1
                        logger.warning(
                            "BybitTickStream: connection lost, reconnecting in %.1fs "
                            "(attempt %d)...",
                            backoff,
                            _reconnect_attempt,
                        )
                        await asyncio.sleep(backoff)
                        try:
                            await self._connect()
                        except Exception as reconnect_exc:
                            logger.error(
                                "BybitTickStream: reconnect attempt %d failed: %s",
                                _reconnect_attempt,
                                reconnect_exc,
                            )
                except Exception as exc:
                    self.stats.errors += 1
                    logger.warning("BybitTickStream reader error: %s", exc)
                    await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass

    def _parse_message(self, msg: Dict[str, Any]) -> Optional[TickEvent]:
        """Parse Bybit orderbook.1 message into a TickEvent."""
        if msg.get("topic") != f"orderbook.1.{self.symbol}":
            return None
        data = msg.get("data", {})
        bids = data.get("b") or []
        asks = data.get("a") or []
        if not bids or not asks:
            return None
        try:
            bid = float(bids[0][0])
            ask = float(asks[0][0])
            ts_ms = float(msg.get("ts") or time.time() * 1000)
            ts = ts_ms / 1000.0
            spread_pips = (ask - bid) / self._pip_size if self._pip_size > 0 else 0.0
            return TickEvent(
                symbol=self.symbol,
                bid=bid,
                ask=ask,
                timestamp=ts,
                spread_pips=spread_pips,
                last_price=(bid + ask) / 2.0,
                quote_id=f"bybit:{self.symbol}:{int(ts_ms)}",
                source="bybit",
            )
        except (IndexError, ValueError, TypeError):
            return None

    async def __aiter__(self) -> AsyncIterator[TickEvent]:  # type: ignore[override]
        while self._running:
            try:
                tick = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                yield tick
            except asyncio.TimeoutError:
                continue


# ---------------------------------------------------------------------------
# MT5 Bridge tick stream (polling the bridge /quote endpoint)
# ---------------------------------------------------------------------------


class MT5BridgeTickStream(ITickStream):
    """Tick stream backed by the MT5 HTTP Bridge (/quote polling).

    Since MT5 does not expose a native WebSocket quote feed in the Python SDK,
    we poll the bridge's /quote endpoint at a configurable interval.  This is
    adequate for M1+ strategies; for sub-second precision use cTrader or Bybit.
    """

    def __init__(
        self,
        bridge_url: str,
        symbol: str,
        api_key: str = "",
        pip_size: float = 0.0001,
        poll_interval: float = 0.5,
    ) -> None:
        super().__init__(symbol)
        self._bridge_url = str(bridge_url).rstrip("/")
        self._api_key = str(api_key or "")
        self._pip_size = max(1e-12, float(pip_size))
        self._poll_interval = max(0.1, float(poll_interval))
        self._session = None
        self._queue: asyncio.Queue[TickEvent] = asyncio.Queue(maxsize=500)
        self._poll_task: Optional[asyncio.Task] = None

    def _headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        if self._api_key:
            headers["x-api-key"] = self._api_key
        return headers

    async def _connect(self) -> None:
        try:
            import aiohttp

            self._session = aiohttp.ClientSession()
        except ImportError as exc:
            raise RuntimeError(
                "MT5BridgeTickStream requires the aiohttp package. "
                "Install: pip install aiohttp"
            ) from exc
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("MT5BridgeTickStream connected: %s → %s", self.symbol, self._bridge_url)

    async def _disconnect(self) -> None:
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        if self._session is not None:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None
        logger.info("MT5BridgeTickStream disconnected: %s", self.symbol)

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                url = f"{self._bridge_url}/quote"
                async with self._session.get(  # type: ignore[union-attr]
                    url, params={"symbol": self.symbol}, headers=self._headers()
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data and isinstance(data, dict):
                            tick = self._parse_quote(data)
                            if tick is not None:
                                await self._queue.put(tick)
                                self._record_tick()
                    else:
                        self.stats.errors += 1
            except Exception as exc:
                self.stats.errors += 1
                logger.debug("MT5BridgeTickStream poll error: %s", exc)
            await asyncio.sleep(self._poll_interval)

    def _parse_quote(self, data: Dict[str, Any]) -> Optional[TickEvent]:
        try:
            bid = float(data["bid"])
            ask = float(data["ask"])
            ts = float(data.get("timestamp") or time.time())
            spread_pips = float(data.get("spread_pips") or (ask - bid) / self._pip_size)
            return TickEvent(
                symbol=self.symbol,
                bid=bid,
                ask=ask,
                timestamp=ts,
                spread_pips=spread_pips,
                last_price=(bid + ask) / 2.0,
                quote_id=str(data.get("quote_id") or f"mt5bridge:{self.symbol}:{int(ts * 1000)}"),
                source="mt5_bridge",
            )
        except (KeyError, TypeError, ValueError):
            return None

    async def __aiter__(self) -> AsyncIterator[TickEvent]:  # type: ignore[override]
        while self._running:
            try:
                tick = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                yield tick
            except asyncio.TimeoutError:
                continue


# ---------------------------------------------------------------------------
# CTrader tick stream (via broker_provider.get_quote polling)
# ---------------------------------------------------------------------------


class CTraderTickStream(ITickStream):
    """Tick stream backed by CTraderProvider.get_quote() polling.

    The cTrader OpenAPI does support streaming SpotEvents via its protobuf
    WebSocket.  Until the ctrader_provider engine exposes a native async
    stream, we bridge via the provider's get_quote() method at a configurable
    poll rate.

    For true streaming, the underlying CTraderDataProvider should be updated
    to subscribe to ProtoOASpotEvent and push into an asyncio.Queue; this class
    will then wrap that queue with zero extra latency.
    """

    def __init__(
        self,
        provider: Any,
        symbol: str,
        pip_size: float = 0.0001,
        poll_interval: float = 0.25,
    ) -> None:
        super().__init__(symbol)
        self._provider = provider
        self._pip_size = max(1e-12, float(pip_size))
        self._poll_interval = max(0.05, float(poll_interval))
        self._queue: asyncio.Queue[TickEvent] = asyncio.Queue(maxsize=500)
        self._poll_task: Optional[asyncio.Task] = None

    async def _connect(self) -> None:
        # Ensure provider is connected
        if hasattr(self._provider, "is_connected") and not self._provider.is_connected:
            connect_fn = getattr(self._provider, "connect", None)
            if callable(connect_fn):
                await connect_fn()
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("CTraderTickStream connected: %s", self.symbol)

    async def _disconnect(self) -> None:
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        logger.info("CTraderTickStream disconnected: %s", self.symbol)

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                get_quote = getattr(self._provider, "get_quote", None)
                if callable(get_quote):
                    data = await get_quote(self.symbol)
                    if data and isinstance(data, dict):
                        tick = self._parse_quote(data)
                        if tick is not None:
                            await self._queue.put(tick)
                            self._record_tick()
            except Exception as exc:
                self.stats.errors += 1
                logger.debug("CTraderTickStream poll error: %s", exc)
            await asyncio.sleep(self._poll_interval)

    def _parse_quote(self, data: Dict[str, Any]) -> Optional[TickEvent]:
        try:
            bid = float(data["bid"])
            ask = float(data["ask"])
            ts = float(data.get("timestamp") or time.time())
            spread_pips = float(
                data.get("spread_pips") or (ask - bid) / self._pip_size
            )
            return TickEvent(
                symbol=self.symbol,
                bid=bid,
                ask=ask,
                timestamp=ts,
                spread_pips=spread_pips,
                last_price=(bid + ask) / 2.0,
                quote_id=str(data.get("quote_id") or f"ctrader:{self.symbol}:{int(ts * 1000)}"),
                source="ctrader",
            )
        except (KeyError, TypeError, ValueError):
            return None

    async def __aiter__(self) -> AsyncIterator[TickEvent]:  # type: ignore[override]
        while self._running:
            try:
                tick = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                yield tick
            except asyncio.TimeoutError:
                continue
