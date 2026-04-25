"""Signal feed — async generator of TradingSignal events."""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, List

from .signal_builder import TradingSignal

logger = logging.getLogger(__name__)


class SignalFeed:
    """
    Async feed of TradingSignal objects.
    Signals are pushed via put() and consumed via __aiter__.
    """

    def __init__(self, maxsize: int = 1000) -> None:
        self._queue: asyncio.Queue[TradingSignal] = asyncio.Queue(maxsize=maxsize)

    async def put(self, signal: TradingSignal) -> None:
        await self._queue.put(signal)
        logger.debug("Signal queued: %s %s", signal.symbol, signal.direction)

    def put_nowait(self, signal: TradingSignal) -> None:
        self._queue.put_nowait(signal)

    async def get(self) -> TradingSignal:
        return await self._queue.get()

    def empty(self) -> bool:
        return self._queue.empty()

    async def drain(self) -> List[TradingSignal]:
        signals: List[TradingSignal] = []
        while not self._queue.empty():
            try:
                signals.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return signals

    async def __aiter__(self) -> AsyncIterator[TradingSignal]:
        while True:
            signal = await self._queue.get()
            yield signal
