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
from typing import Any, Awaitable, Callable, Dict, Optional

from .runtime_state import RuntimeState, RuntimeStatus

logger = logging.getLogger(__name__)

SignalHook = Callable[[Dict[str, Any]], Awaitable[None]]
OrderHook = Callable[[Dict[str, Any]], Awaitable[None]]
TradeHook = Callable[[Dict[str, Any]], Awaitable[None]]
TradeUpdateHook = Callable[[Dict[str, Any]], Awaitable[None]]
SnapshotHook = Callable[[Dict[str, Any]], Awaitable[None]]
EventHook = Callable[[str, Dict[str, Any]], Awaitable[None]]


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
        runtime_mode: str = "paper",
        ai_config: Optional[Dict[str, Any]] = None,
        on_signal: Optional[SignalHook] = None,
        on_order: Optional[OrderHook] = None,
        on_trade: Optional[TradeHook] = None,
        on_trade_update: Optional[TradeUpdateHook] = None,
        on_snapshot: Optional[SnapshotHook] = None,
        on_event: Optional[EventHook] = None,
        reserve_idempotency: Optional[Callable[[str], Awaitable[bool]]] = None,
        get_daily_state: Optional[Callable[[], Awaitable[Dict[str, Any] | None]]] = None,
    ) -> None:
        self.bot_instance_id = bot_instance_id
        self.strategy_config = strategy_config
        self.broker_provider = broker_provider
        self.risk_config = risk_config
        self.runtime_mode = str(runtime_mode or "paper").lower()
        self.ai_config = ai_config or {}
        self.state = RuntimeState(bot_instance_id=bot_instance_id)
        self._engine_task: Optional[asyncio.Task] = None
        self._tick_interval: float = 5.0
        self._lifecycle_lock = asyncio.Lock()
        self._on_signal = on_signal
        self._on_order = on_order
        self._on_trade = on_trade
        self._on_trade_update = on_trade_update
        self._on_snapshot = on_snapshot
        self._on_event = on_event
        self._reserve_idempotency = reserve_idempotency
        self._get_daily_state = get_daily_state
        self._known_trade_volumes: Dict[str, float] = {}
        self._known_remaining_volumes: Dict[str, float] = {}
        self._closed_trade_ids: set[str] = set()

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
        self._brain = None
        self._execution_engine = None
        self._gate = None
        self._consecutive_losses: int = 0
        self._daily_profit_amount: float = 0.0
        self._daily_loss_pct: float = 0.0

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
            from trading_core.engines.signal_coordinator import TradeSignal

            try:
                from execution_service.execution_engine import ExecutionEngine
            except ImportError:
                ExecutionEngine = None

            try:
                from ai_trading_brain.brain_runtime import ForexBrainRuntime
            except ImportError:
                ForexBrainRuntime = None

            self._wave_detector = WaveDetector()
            self._signal_coordinator = SignalCoordinator()
            self._risk_manager = RiskManager()
            self._entry_logic = EntryLogic()
            self._trade_manager = TradeManager()
            self._decision_engine = DecisionEngine()
            self._capital_manager = CapitalManager()
            self._auto_pilot = AutoPilot()
            self._signal_coordinator.start()
            self._signal_coordinator.set_execute_callback(self._execute_signal)

            if ExecutionEngine is not None:
                self._execution_engine = ExecutionEngine(
                    provider=self.broker_provider,
                    provider_name=self.bot_instance_id,
                )

            if ForexBrainRuntime is not None:
                self._brain = ForexBrainRuntime(
                    policy=self.ai_config.get("policy") if isinstance(self.ai_config, dict) else None,
                    governance_config=(
                        self.ai_config.get("governance")
                        if isinstance(self.ai_config, dict)
                        else None
                    ),
                )

            from trading_core.runtime.pre_execution_gate import PreExecutionGate
            gate_policy = self.risk_config.get("gate_policy", {}) if isinstance(self.risk_config, dict) else {}
            self._gate = PreExecutionGate(policy=gate_policy)

            logger.info("Engines initialised for bot: %s", self.bot_instance_id)
        except Exception as exc:
            logger.error("Engine init failed for %s: %s", self.bot_instance_id, exc)
            raise

    # ── Lifecycle ──────────────────────────────────────────────────────── #

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self.state.status in (RuntimeStatus.RUNNING, RuntimeStatus.STARTING):
                logger.warning("BotRuntime %s already running or starting", self.bot_instance_id)
                return
            self.state.status = RuntimeStatus.STARTING
            self._init_engines()
            await self._ensure_provider_usable()
            if self._execution_engine is not None:
                await self._execution_engine.start()
            self.state.started_at = time.time()
            self.state.status = RuntimeStatus.RUNNING
            self._engine_task = asyncio.create_task(self._run_loop())
            logger.info("BotRuntime started: %s", self.bot_instance_id)

    async def stop(self) -> None:
        async with self._lifecycle_lock:
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
            if self._execution_engine is not None:
                try:
                    await self._execution_engine.stop()
                except Exception as exc:
                    logger.warning(
                        "Execution engine stop failed for %s: %s",
                        self.bot_instance_id,
                        exc,
                    )
            if hasattr(self.broker_provider, "disconnect"):
                try:
                    await self.broker_provider.disconnect()
                except Exception as exc:
                    logger.warning(
                        "Broker disconnect failed for %s: %s",
                        self.bot_instance_id,
                        exc,
                    )
            logger.info("BotRuntime stopped: %s", self.bot_instance_id)

    async def pause(self) -> None:
        async with self._lifecycle_lock:
            if self.state.status == RuntimeStatus.RUNNING:
                self.state.status = RuntimeStatus.PAUSED

    async def resume(self) -> None:
        async with self._lifecycle_lock:
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
            if signal and signal.get("direction") in {"BUY", "SELL"}:
                await self._persist_signal(signal)
                trade_signal = self._build_trade_signal(signal, df)
                status = self._signal_coordinator.submit_signal(trade_signal)
                self.state.metadata["last_submit_status"] = status
                await self._signal_coordinator.process_all(
                    lambda direction: self._wave_detector.can_trade(direction, wave)
                )
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
        df = await self.broker_provider.get_candles(
            symbol=symbol,
            timeframe=timeframe,
            limit=200,
        )
        self.state.metadata["market_data_ok"] = bool(df is not None and not df.empty)
        if df is not None and not df.empty:
            last_idx = df.index[-1]
            try:
                ts = float(last_idx.timestamp())
            except Exception:
                ts = time.time()
            self.state.metadata["last_market_data_ts"] = ts
            self.state.metadata["data_age_seconds"] = max(0.0, time.time() - ts)
        return df

    def _analyse_market(self, df):
        return self._wave_detector.analyse(df)

    def _generate_signal(self, df, wave):
        wave_state = str(getattr(getattr(wave, "main_wave", ""), "value", getattr(wave, "main_wave", "")))
        direction = "HOLD"
        if wave_state == "BULL_MAIN":
            direction = "BUY"
        elif wave_state == "BEAR_MAIN":
            direction = "SELL"

        entry_price = float(df["close"].iloc[-1])
        signal = {
            "signal_id": f"{self.bot_instance_id}-{int(time.time() * 1000)}",
            "symbol": getattr(self.broker_provider, "symbol", "EURUSD"),
            "wave_state": wave_state,
            "confidence": float(getattr(wave, "confidence", 0.0)),
            "direction": direction,
            "entry_price": entry_price,
        }

        if direction in {"BUY", "SELL"}:
            if self._brain is None:
                if self.runtime_mode == "live":
                    self.state.error_message = "brain_unavailable_in_live_mode"
                    signal["direction"] = "HOLD"
                    signal["brain_action"] = "BLOCK"
                    signal["brain_reason"] = self.state.error_message
                    return signal
                return signal
            try:
                from ai_trading_brain.brain_contracts import BrainInput

                brain_input = BrainInput(
                    symbol=str(signal["symbol"]),
                    timeframe=getattr(self.broker_provider, "timeframe", "M5"),
                    broker=getattr(self.broker_provider, "provider_name", "stub"),
                    market={
                        "close": entry_price,
                        "wave_state": wave_state,
                        "confidence": signal["confidence"],
                    },
                    account={"equity": self.state.equity},
                    positions=[],
                    signals=[signal],
                )
                cycle_result = self._brain.run_cycle(brain_input)
                action = str(getattr(cycle_result.action, "value", cycle_result.action)).upper()
                if not getattr(cycle_result, "cycle_id", None):
                    raise RuntimeError("brain_cycle_missing_cycle_id")
                signal["brain_action"] = action
                signal["brain_cycle_id"] = cycle_result.cycle_id
                signal["brain_reason"] = cycle_result.reason
                signal["brain_score"] = float(cycle_result.final_score)
                if action in {"BLOCK", "SKIP", "PAUSE", "HOLD"}:
                    signal["direction"] = "HOLD"
                elif cycle_result.execution_intent is not None:
                    intent_side = str(cycle_result.execution_intent.side).upper()
                    signal["direction"] = intent_side if intent_side in {"BUY", "SELL"} else "HOLD"
                self.state.metadata["last_brain_cycle"] = cycle_result.to_dict()
                if self._on_event:
                    asyncio.create_task(self._safe_hook(self._on_event("brain_cycle", cycle_result.to_dict()), "brain_cycle"))
            except Exception as exc:
                logger.warning("Brain run_cycle failed [%s]: %s", self.bot_instance_id, exc)
                if self.runtime_mode == "live":
                    self.state.error_message = f"brain_run_cycle_failed: {exc}"
                    signal["direction"] = "HOLD"
                    signal["brain_action"] = "BLOCK"
                    signal["brain_reason"] = self.state.error_message

        return signal

    def _build_trade_signal(self, signal: Dict[str, Any], df):
        from trading_core.engines.signal_coordinator import TradeSignal

        entry_price = float(signal.get("entry_price") or df["close"].iloc[-1])
        atr = float((df["high"].tail(14) - df["low"].tail(14)).mean() or 0.0)
        if atr <= 0:
            atr = entry_price * 0.001

        direction = str(signal.get("direction") or "HOLD").upper()
        lot_size = float(self.risk_config.get("lot_size", 0.01)) if isinstance(self.risk_config, dict) else 0.01
        rr = float(self.strategy_config.get("rr", 2.0)) if isinstance(self.strategy_config, dict) else 2.0
        if direction == "BUY":
            sl = entry_price - atr
            tp = entry_price + atr * rr
        else:
            sl = entry_price + atr
            tp = entry_price - atr * rr

        return TradeSignal(
            signal_id=str(signal.get("signal_id")),
            symbol=str(signal.get("symbol", "EURUSD")),
            direction=direction,
            entry_price=entry_price,
            sl=float(sl),
            tp=float(tp),
            lot_size=max(0.01, lot_size),
            entry_mode="runtime_auto",
            priority=5,
            meta={"wave_state": signal.get("wave_state", "")},
        )

    async def _persist_signal(self, signal: Dict[str, Any]) -> None:
        self.state.metadata["last_signal"] = signal
        if self._on_signal:
            await self._safe_hook(self._on_signal(signal), "on_signal")

    async def _execute_signal(self, signal) -> None:
        if self._execution_engine is None:
            logger.warning("Execution engine unavailable for %s", self.bot_instance_id)
            return

        try:
            from execution_service.providers.base import OrderRequest
        except ImportError as exc:
            logger.warning("OrderRequest import failed: %s", exc)
            return

        request = OrderRequest(
            symbol=signal.symbol,
            side=signal.direction.lower(),
            volume=float(signal.lot_size),
            order_type="market",
            price=float(signal.entry_price),
            stop_loss=float(signal.sl),
            take_profit=float(signal.tp),
            comment=str(signal.signal_id),
        )

        # ── P0: Pre-execution gate ─────────────────────────────────────
        if self._gate is not None:
            entry = float(signal.entry_price)
            sl = float(signal.sl)
            rr = abs((float(signal.tp) - entry) / (entry - sl)) if abs(entry - sl) > 0 else 0.0
            spread_pips = float(getattr(self.broker_provider, "spread_pips", 0.0))
            idempotency_key = str(signal.signal_id)
            daily_state = await self._get_daily_state() if self._get_daily_state else None
            if self.runtime_mode == "live" and daily_state is None:
                self.state.error_message = "daily_state_unavailable"
                return
            gate_ctx = {
                "provider_mode": str(getattr(self.broker_provider, "mode", "stub")),
                "runtime_mode": self.runtime_mode,
                "broker_connected": bool(getattr(self.broker_provider, "is_connected", False)),
                "market_data_ok": bool(self.state.metadata.get("market_data_ok", False)),
                "data_age_seconds": float(self.state.metadata.get("data_age_seconds", 10**9)),
                "daily_profit_amount": float((daily_state or {}).get("daily_profit_amount", self._daily_profit_amount)),
                "daily_loss_pct": float((daily_state or {}).get("daily_loss_pct", self._daily_loss_pct)),
                "consecutive_losses": int((daily_state or {}).get("consecutive_losses", self._consecutive_losses)),
                "spread_pips": spread_pips,
                "confidence": float(getattr(signal, "meta", {}).get("confidence", 1.0)),
                "rr": rr,
                "open_positions": int(self.state.open_trades),
                "idempotency_exists": False,
                "kill_switch": bool(self.state.metadata.get("kill_switch", False)) or bool((daily_state or {}).get("locked", False)),
            }
            gate_result = self._gate.evaluate(gate_ctx)
            gate_event = {
                "bot_instance_id": self.bot_instance_id,
                "signal_id": str(signal.signal_id),
                "idempotency_key": idempotency_key,
                "gate_action": gate_result.action,
                "gate_reason": gate_result.reason,
                "gate_details": gate_result.details,
            }
            if self._on_event:
                await self._safe_hook(self._on_event("gate_evaluated", gate_event), "gate_event")
            if gate_result.action != "ALLOW":
                logger.info("Gate %s for signal %s: %s", gate_result.action, signal.signal_id, gate_result.reason)
                if gate_result.action == "BLOCK":
                    self._consecutive_losses += 1
                return

            # Reserve idempotency in DB before broker call (fail-closed in live)
            if self._reserve_idempotency is not None:
                reserved = await self._reserve_idempotency(idempotency_key)
                if not reserved:
                    dup_event = {
                        "bot_instance_id": self.bot_instance_id,
                        "signal_id": str(signal.signal_id),
                        "idempotency_key": idempotency_key,
                        "gate_action": "BLOCK",
                        "gate_reason": "duplicate_order_blocked",
                        "gate_details": {"source": "db_reservation"},
                    }
                    if self._on_event:
                        await self._safe_hook(self._on_event("gate_evaluated", dup_event), "gate_event")
                    return
            elif self.runtime_mode == "live":
                self.state.error_message = "idempotency_service_unavailable"
                return
        # ── end gate ───────────────────────────────────────────────────

        result = await self._execution_engine.place_order(request)
        order_payload = {
            "bot_instance_id": self.bot_instance_id,
            "signal_id": signal.signal_id,
            "broker_order_id": result.order_id,
            "symbol": result.symbol,
            "side": result.side.upper(),
            "order_type": request.order_type,
            "volume": result.volume,
            "price": result.fill_price,
            "status": "filled" if result.success else "rejected",
            "error_message": result.error_message,
        }
        self.state.metadata["last_order"] = order_payload
        if self._on_order:
            await self._safe_hook(self._on_order(order_payload), "on_order")

        if not result.success:
            self.state.error_message = result.error_message
            self._consecutive_losses += 1
            await self._emit_event("order_rejected", order_payload)
            return

        self._consecutive_losses = 0
        self.state.total_trades += 1
        trade_payload = {
            "bot_instance_id": self.bot_instance_id,
            "broker_trade_id": result.order_id,
            "symbol": result.symbol,
            "side": result.side.upper(),
            "volume": result.volume,
            "entry_price": result.fill_price,
            "stop_loss": request.stop_loss,
            "take_profit": request.take_profit,
            "commission": result.commission,
            "status": "open",
            "closed_volume": 0.0,
            "remaining_volume": result.volume,
        }
        self.state.metadata["last_trade"] = trade_payload
        self._known_trade_volumes[result.order_id] = float(result.volume)
        self._known_remaining_volumes[result.order_id] = float(result.volume)
        if self._on_trade:
            await self._safe_hook(self._on_trade(trade_payload), "on_trade")
        await self._emit_event("order_filled", order_payload)
        await self._emit_event("trade_opened", trade_payload)

    async def _manage_trades(self, signal: Dict[str, Any]) -> None:
        self.state.metadata["last_signal"] = signal
        if self._execution_engine is not None:
            try:
                positions = await self._execution_engine.get_open_positions()
                self.state.open_trades = len(positions)
                account_info = self._execution_engine.account_info
                if account_info:
                    self.state.balance = float(account_info.get("balance", self.state.balance))
                    self.state.equity = float(account_info.get("equity", self.state.equity))
                await self._sync_trade_lifecycle(positions)
            except Exception as exc:
                logger.warning("Trade management sync failed [%s]: %s", self.bot_instance_id, exc)

    async def close_position(self, position_id: str) -> Dict[str, Any]:
        if self._execution_engine is None:
            raise RuntimeError("Execution engine unavailable")
        result = await self._execution_engine.close_position(position_id)
        if not result.success:
            raise RuntimeError(result.error_message or "Close position failed")

        original_volume = self._known_trade_volumes.get(position_id, float(result.volume))
        close_payload = {
            "bot_instance_id": self.bot_instance_id,
            "broker_trade_id": position_id,
            "symbol": result.symbol,
            "status": "closed",
            "exit_price": float(result.fill_price),
            "pnl": None,
            "closed_volume": float(result.volume),
            "remaining_volume": max(0.0, original_volume - float(result.volume)),
        }
        self._known_remaining_volumes[position_id] = close_payload["remaining_volume"]
        self._closed_trade_ids.add(position_id)
        if self._on_trade_update:
            await self._safe_hook(self._on_trade_update(close_payload), "on_trade_update")
        await self._emit_event("trade_closed", close_payload)
        return close_payload

    async def submit_manual_signal(
        self,
        direction: str = "BUY",
        confidence: float = 0.95,
    ) -> Dict[str, Any]:
        df = await self._fetch_market_data()
        if df is None or df.empty:
            raise RuntimeError("No market data available")

        wave = self._analyse_market(df)
        signal = {
            "signal_id": f"manual-{self.bot_instance_id}-{int(time.time() * 1000)}",
            "symbol": getattr(self.broker_provider, "symbol", "EURUSD"),
            "wave_state": str(getattr(getattr(wave, "main_wave", ""), "value", "")),
            "confidence": float(confidence),
            "direction": str(direction).upper(),
            "entry_price": float(df["close"].iloc[-1]),
        }
        if signal["direction"] not in {"BUY", "SELL"}:
            raise RuntimeError("direction must be BUY or SELL")

        await self._persist_signal(signal)
        trade_signal = self._build_trade_signal(signal, df)
        status = self._signal_coordinator.submit_signal(trade_signal)
        await self._signal_coordinator.process_all(lambda _d: True)
        self.state.metadata["last_submit_status"] = status
        return signal

    async def _sync_trade_lifecycle(self, positions: list[dict]) -> None:
        open_map: Dict[str, dict] = {}
        for pos in positions:
            pid = self._extract_position_id(pos)
            if not pid:
                continue
            open_map[pid] = pos

        for pid, pos in open_map.items():
            current_volume = self._extract_position_volume(pos)
            self._known_remaining_volumes.setdefault(pid, current_volume)
            if pid not in self._known_trade_volumes:
                self._known_trade_volumes[pid] = current_volume
            original_volume = self._known_trade_volumes.get(pid, current_volume)
            previous_remaining = self._known_remaining_volumes.get(pid, original_volume)
            if current_volume < previous_remaining and current_volume > 0:
                update_payload = {
                    "bot_instance_id": self.bot_instance_id,
                    "broker_trade_id": pid,
                    "status": "partial",
                    "closed_volume": max(0.0, original_volume - current_volume),
                    "remaining_volume": current_volume,
                }
                self._known_remaining_volumes[pid] = current_volume
                if self._on_trade_update:
                    await self._safe_hook(self._on_trade_update(update_payload), "on_trade_update")
                await self._emit_event("trade_partial", update_payload)

        history: list[dict] = []
        try:
            history = await self._execution_engine.get_trade_history(limit=200)
        except Exception as exc:
            logger.debug("History sync skipped [%s]: %s", self.bot_instance_id, exc)

        for item in history:
            pid = self._extract_position_id(item)
            if not pid or pid in self._closed_trade_ids:
                continue
            if pid in open_map:
                continue

            original_volume = self._known_trade_volumes.get(pid, self._extract_position_volume(item))
            close_volume = self._extract_position_volume(item)
            update_payload = {
                "bot_instance_id": self.bot_instance_id,
                "broker_trade_id": pid,
                "symbol": str(item.get("symbol", "")),
                "status": "closed",
                "exit_price": self._extract_exit_price(item),
                "pnl": self._extract_pnl(item),
                "closed_volume": close_volume or original_volume,
                "remaining_volume": 0.0,
            }
            self._closed_trade_ids.add(pid)
            self._known_remaining_volumes[pid] = 0.0
            if self._on_trade_update:
                await self._safe_hook(self._on_trade_update(update_payload), "on_trade_update")
            await self._emit_event("trade_closed", update_payload)

    def _extract_position_id(self, payload: Dict[str, Any]) -> str:
        for key in ("position_id", "trade_id", "id", "order_id", "broker_trade_id"):
            value = payload.get(key)
            if value is not None:
                return str(value)
        return ""

    def _extract_position_volume(self, payload: Dict[str, Any]) -> float:
        for key in ("remaining_volume", "volume", "qty", "size"):
            value = payload.get(key)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    def _extract_exit_price(self, payload: Dict[str, Any]) -> Optional[float]:
        for key in ("close_price", "exit_price", "fill_price", "executionPrice"):
            value = payload.get(key)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return None
        return None

    def _extract_pnl(self, payload: Dict[str, Any]) -> Optional[float]:
        for key in ("pnl", "profit"):
            value = payload.get(key)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return None
        return None

    async def _persist_snapshot(self, df, wave, signal: Dict[str, Any]) -> None:
        self.state.metadata["last_tick_at"] = time.time()
        self.state.metadata["last_candle_close"] = float(df["close"].iloc[-1])
        self.state.metadata["last_wave_confidence"] = float(getattr(wave, "confidence", 0.0))
        self.state.metadata["last_signal"] = signal
        snapshot = self.state.to_dict()
        if self._on_snapshot:
            await self._safe_hook(self._on_snapshot(snapshot), "on_snapshot")

    async def _publish_realtime_event(self, wave, signal: Dict[str, Any]) -> None:
        payload = {
            "bot_instance_id": self.bot_instance_id,
            "wave_state": str(getattr(wave, "main_wave", "")),
            "confidence": signal.get("confidence", 0.0),
        }
        self.state.metadata["last_event"] = payload
        await self._emit_event("tick", payload)

    async def _update_broker_health(self) -> None:
        connected = bool(getattr(self.broker_provider, "is_connected", False))
        status = "disconnected"
        reason = "provider_not_connected"
        if connected:
            status = "healthy"
            reason = ""
        health_check = getattr(self.broker_provider, "health_check", None)
        if callable(health_check):
            try:
                details = await health_check()
                if isinstance(details, dict):
                    status = str(details.get("status") or status)
                    reason = str(details.get("reason") or reason)
            except Exception:
                status = "degraded"
                reason = "health_check_failed"
        self.state.metadata["broker_connected"] = connected
        self.state.metadata["broker_health"] = {"status": status, "reason": reason}

        if self.runtime_mode == "live" and str(status).lower() in {
            "auth_failed",
            "disconnected",
            "degraded",
            "error",
        }:
            self.state.status = RuntimeStatus.ERROR
            self.state.error_message = reason or f"Provider health: {status}"
            raise RuntimeError(self.state.error_message)

    async def _ensure_provider_usable(self) -> None:
        try:
            if hasattr(self.broker_provider, "connect") and not getattr(self.broker_provider, "is_connected", False):
                await self.broker_provider.connect()
            if not getattr(self.broker_provider, "is_connected", False):
                self.state.status = RuntimeStatus.ERROR
                self.state.error_message = "Broker provider unavailable"
                raise RuntimeError("Broker provider is not connected")

            provider_mode = str(getattr(self.broker_provider, "mode", "unknown")).lower()
            if self.runtime_mode == "live" and provider_mode in {"stub", "unavailable", "degraded", "paper"}:
                self.state.status = RuntimeStatus.ERROR
                self.state.error_message = f"provider_mode_not_allowed:{provider_mode}"
                raise RuntimeError(self.state.error_message)

            account_info_fn = getattr(self.broker_provider, "get_account_info", None)
            if self.runtime_mode == "live" and callable(account_info_fn):
                info = await account_info_fn()
                if info is None or float(getattr(info, "equity", 0.0)) <= 0:
                    self.state.status = RuntimeStatus.ERROR
                    self.state.error_message = "invalid_account_info"
                    raise RuntimeError(self.state.error_message)

            # live startup sanity: verify candles are available
            if self.runtime_mode == "live":
                sample = await self._fetch_market_data()
                if sample is None or sample.empty:
                    self.state.status = RuntimeStatus.ERROR
                    self.state.error_message = "market_data_unavailable"
                    raise RuntimeError(self.state.error_message)
                if self._brain is None:
                    self.state.status = RuntimeStatus.ERROR
                    self.state.error_message = "brain_unavailable_in_live_mode"
                    raise RuntimeError(self.state.error_message)

            health_check = getattr(self.broker_provider, "health_check", None)
            if callable(health_check):
                details = await health_check()
                if isinstance(details, dict):
                    status = str(details.get("status", "healthy")).lower()
                    if status in {"auth_failed", "disconnected", "degraded", "error"}:
                        self.state.status = RuntimeStatus.ERROR
                        self.state.error_message = str(details.get("reason") or f"Provider health: {status}")
                        raise RuntimeError(self.state.error_message)
        except Exception:
            if self.state.status != RuntimeStatus.ERROR:
                self.state.status = RuntimeStatus.ERROR
            raise

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

    async def _emit_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        if self._on_event:
            await self._safe_hook(self._on_event(event_type, payload), "on_event")

    async def _safe_hook(self, awaitable: Awaitable[None], hook_name: str) -> None:
        try:
            await awaitable
        except Exception as exc:
            logger.warning("Hook %s failed for %s: %s", hook_name, self.bot_instance_id, exc)
