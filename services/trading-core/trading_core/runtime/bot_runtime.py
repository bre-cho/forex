"""
BotRuntime — per-bot-instance runtime wrapper.

Each BotRuntime holds its own isolated set of engine components.
The RuntimeRegistry manages multiple BotRuntime instances, enabling
true multi-user / multi-bot operation.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional

from .runtime_state import RuntimeState, RuntimeStatus

logger = logging.getLogger(__name__)


class BotRuntime:
    """
    Per-bot-instance runtime. Replaces the global AppState singleton.

    Each bot gets its own:
    - WaveDetector
    - SignalCoordinator
    - RiskManager
    - EntryLogic
    - TradeManager
    - DecisionEngine
    - CapitalManager
    - LLMOrchestrator
    - DataProvider (via broker_provider)
    """

    def __init__(
        self,
        bot_instance_id: str,
        strategy_config: Dict[str, Any],
        broker_provider: Any,
        risk_config: Dict[str, Any],
        ai_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.bot_instance_id = bot_instance_id
        self.strategy_config = strategy_config
        self.broker_provider = broker_provider
        self.risk_config = risk_config
        self.ai_config = ai_config or {}
        self.state = RuntimeState(bot_instance_id=bot_instance_id)
        self._engine_task: Optional[asyncio.Task] = None
        self._tick_interval: float = 5.0

        # Lazy-initialised engines (created on start)
        self._wave_detector = None
        self._signal_coordinator = None
        self._risk_manager = None
        self._entry_logic = None
        self._trade_manager = None
        self._decision_engine = None
        self._capital_manager = None
        self._llm = None
        self._auto_pilot = None

        logger.info("BotRuntime created: %s", bot_instance_id)

    # ── Engine lazy init ───────────────────────────────────────────────── #

    def _init_engines(self) -> None:
        """Initialise all engine components from config."""
        try:
            from trading_core.engines.wave_detector import WaveDetector
            from trading_core.engines.signal_coordinator import SignalCoordinator
            from trading_core.engines.risk_manager import RiskManager
            from trading_core.engines.entry_logic import EntryLogic
            from trading_core.engines.trade_manager import TradeManager
            from trading_core.engines.decision_engine import DecisionEngine
            from trading_core.engines.capital_manager import CapitalManager
            from trading_core.engines.auto_pilot import AutoPilot

            self._wave_detector = WaveDetector()
            self._signal_coordinator = SignalCoordinator()
            self._risk_manager = RiskManager()
            self._entry_logic = EntryLogic()
            self._trade_manager = TradeManager()
            self._decision_engine = DecisionEngine()
            self._capital_manager = CapitalManager()
            self._auto_pilot = AutoPilot()

            logger.info("Engines initialised for bot: %s", self.bot_instance_id)
        except Exception as exc:
            logger.error("Engine init failed for %s: %s", self.bot_instance_id, exc)
            raise

    # ── Lifecycle ──────────────────────────────────────────────────────── #

    async def start(self) -> None:
        if self.state.status == RuntimeStatus.RUNNING:
            logger.warning("BotRuntime %s already running", self.bot_instance_id)
            return
        self.state.status = RuntimeStatus.STARTING
        self._init_engines()
        self.state.started_at = time.time()
        self.state.status = RuntimeStatus.RUNNING
        self._engine_task = asyncio.create_task(self._run_loop())
        logger.info("BotRuntime started: %s", self.bot_instance_id)

    async def stop(self) -> None:
        if self.state.status == RuntimeStatus.STOPPED:
            return
        self.state.status = RuntimeStatus.STOPPED
        self.state.stopped_at = time.time()
        if self._engine_task:
            self._engine_task.cancel()
            try:
                await self._engine_task
            except asyncio.CancelledError:
                pass
            self._engine_task = None
        logger.info("BotRuntime stopped: %s", self.bot_instance_id)

    async def pause(self) -> None:
        if self.state.status == RuntimeStatus.RUNNING:
            self.state.status = RuntimeStatus.PAUSED

    async def resume(self) -> None:
        if self.state.status == RuntimeStatus.PAUSED:
            self.state.status = RuntimeStatus.RUNNING

    # ── Runtime loop ───────────────────────────────────────────────────── #

    async def _run_loop(self) -> None:
        logger.info("Engine loop started: %s", self.bot_instance_id)
        while self.state.status in (RuntimeStatus.RUNNING, RuntimeStatus.PAUSED):
            try:
                if self.state.status == RuntimeStatus.RUNNING:
                    await self.tick()
            except Exception as exc:
                logger.error("Tick error [%s]: %s", self.bot_instance_id, exc)
                self.state.error_message = str(exc)
            await asyncio.sleep(self._tick_interval)

    async def tick(self) -> None:
        """Single trading cycle tick."""
        try:
            df = await self._fetch_market_data()
            if df is None or df.empty:
                return
            wave = self._analyse_market(df)
            signal = self._generate_signal(df, wave)
            await self._manage_trades(signal)
            await self._persist_snapshot(df, wave, signal)
            await self._publish_realtime_event(wave, signal)
            await self._update_broker_health()
        except Exception as exc:
            logger.error("Tick error [%s]: %s", self.bot_instance_id, exc)
            raise

    async def _fetch_market_data(self):
        if hasattr(self.broker_provider, "is_connected") and not self.broker_provider.is_connected:
            await self.broker_provider.connect()
        symbol = getattr(self.broker_provider, "symbol", "EURUSD")
        timeframe = getattr(self.broker_provider, "timeframe", "M5")
        return await self.broker_provider.get_candles(
            symbol=symbol,
            timeframe=timeframe,
            limit=200,
        )

    def _analyse_market(self, df):
        return self._wave_detector.analyse(df)

    def _generate_signal(self, df, wave):
        return {"wave_state": str(getattr(wave, "main_wave", "")), "confidence": getattr(wave, "confidence", 0.0)}

    async def _manage_trades(self, signal: Dict[str, Any]) -> None:
        self.state.metadata["last_signal"] = signal

    async def _persist_snapshot(self, df, wave, signal: Dict[str, Any]) -> None:
        self.state.metadata["last_tick_at"] = time.time()
        self.state.metadata["last_candle_close"] = float(df["close"].iloc[-1])
        self.state.metadata["last_wave_confidence"] = float(getattr(wave, "confidence", 0.0))
        self.state.metadata["last_signal"] = signal

    async def _publish_realtime_event(self, wave, signal: Dict[str, Any]) -> None:
        self.state.metadata["last_event"] = {
            "bot_instance_id": self.bot_instance_id,
            "wave_state": str(getattr(wave, "main_wave", "")),
            "confidence": signal.get("confidence", 0.0),
        }

    async def _update_broker_health(self) -> None:
        self.state.metadata["broker_connected"] = bool(
            getattr(self.broker_provider, "is_connected", False)
        )

    # ── Snapshot ───────────────────────────────────────────────────────── #

    async def get_snapshot(self) -> Dict[str, Any]:
        """Return current runtime state snapshot."""
        snap = self.state.to_dict()
        if self._wave_detector and self._wave_detector.last_analysis:
            wa = self._wave_detector.last_analysis
            snap["wave_state"] = (
                wa.main_wave.value if hasattr(wa.main_wave, "value") else str(wa.main_wave)
            )
            snap["wave_confidence"] = wa.confidence
        return snap
