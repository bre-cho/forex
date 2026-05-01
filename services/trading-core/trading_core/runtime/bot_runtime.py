"""
BotRuntime — per-bot-instance runtime wrapper.

Each BotRuntime holds its own isolated set of engine components.
The RuntimeRegistry manages multiple BotRuntime instances, enabling
true multi-user / multi-bot operation.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
import hashlib
import json
import logging
import time
from typing import Any, Awaitable, Callable, Dict, Optional

from .runtime_state import RuntimeState, RuntimeStatus

logger = logging.getLogger(__name__)


def _stable_hash_payload(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

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
        reserve_idempotency: Optional[Callable[..., Awaitable[bool]]] = None,
        verify_idempotency_reservation: Optional[Callable[[str, str, str | None], Awaitable[bool]]] = None,
        set_idempotency_status: Optional[Callable[[str, str, str | None], Awaitable[bool]]] = None,
        get_daily_state: Optional[Callable[[], Awaitable[Dict[str, Any] | None]]] = None,
        refresh_daily_state_from_broker: Optional[Callable[[float | None], Awaitable[Dict[str, Any] | None]]] = None,
        evaluate_daily_profit_lock: Optional[Callable[[float], Awaitable[Dict[str, Any] | None]]] = None,
        get_portfolio_risk_snapshot: Optional[Callable[[], Awaitable[Dict[str, Any] | None]]] = None,
        get_db_open_trades: Optional[Callable[[], Awaitable[list[Dict[str, Any]]]]] = None,
        get_unknown_order_attempts: Optional[Callable[[], Awaitable[list[Dict[str, Any]]]]] = None,
        get_policy_approval_status: Optional[Callable[[], Awaitable[bool]]] = None,
        on_live_failover_approval_hook: Optional[Callable[[str, Dict[str, Any]], Awaitable[bool]]] = None,
        close_db_trade: Optional[Callable[[str], Awaitable[None]]] = None,
        on_reconciliation_result: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        on_reconciliation_incident: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        on_unknown_order_resolved: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        mark_submitting_hook: Optional[Callable[[str, str], Awaitable[None]]] = None,
        mark_submit_phase_hook: Optional[Callable[[str, str, str, str, str, Dict[str, Any]], Awaitable[None]]] = None,
        enqueue_unknown_hook: Optional[Callable[[str, str, str | None, Dict[str, Any]], Awaitable[None]]] = None,
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
        self._verify_idempotency_reservation = verify_idempotency_reservation
        self._set_idempotency_status = set_idempotency_status
        self._get_daily_state = get_daily_state
        self._refresh_daily_state_from_broker = refresh_daily_state_from_broker
        self._evaluate_daily_profit_lock = evaluate_daily_profit_lock
        self._get_portfolio_risk_snapshot = get_portfolio_risk_snapshot
        self._get_db_open_trades = get_db_open_trades
        self._get_unknown_order_attempts = get_unknown_order_attempts
        self._get_policy_approval_status = get_policy_approval_status
        self._on_live_failover_approval_hook = on_live_failover_approval_hook
        self._close_db_trade = close_db_trade
        self._on_reconciliation_result = on_reconciliation_result
        self._on_reconciliation_incident = on_reconciliation_incident
        self._on_unknown_order_resolved = on_unknown_order_resolved
        self._mark_submitting_hook = mark_submitting_hook
        self._mark_submit_phase_hook = mark_submit_phase_hook
        self._enqueue_unknown_hook = enqueue_unknown_hook
        self._reconciliation_worker = None
        self._known_trade_volumes: Dict[str, float] = {}
        self._known_remaining_volumes: Dict[str, float] = {}
        self._closed_trade_ids: set[str] = set()
        # P1.1: Heartbeat / reconnection loop task (live mode only)
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._heartbeat_interval: float = 30.0
        self._heartbeat_backoff_steps: tuple[float, ...] = (5.0, 30.0, 120.0)
        # P1.2: Account equity sync loop (live mode only)
        self._account_sync_task: Optional[asyncio.Task] = None
        self._account_sync_interval: float = 30.0
        self._starting_equity: float = 0.0

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
                gate_policy = self.risk_config.get("gate_policy", {}) if isinstance(self.risk_config, dict) else {}
                broker_identity = str(getattr(self.broker_provider, "provider_name", "") or "").strip() or "unknown"
                self._execution_engine = ExecutionEngine(
                    provider=self.broker_provider,
                    provider_name=broker_identity,
                    runtime_mode=self.runtime_mode,
                    gate_policy=gate_policy,
                    verify_idempotency_reservation=self._verify_idempotency_reservation,
                    on_live_failover_approval_hook=self._on_live_failover_approval_hook,
                    mark_submitting_hook=self._mark_submitting_hook,
                    mark_submit_phase_hook=self._mark_submit_phase_hook,
                    enqueue_unknown_hook=self._enqueue_unknown_hook,
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
            if self.runtime_mode == "live":
                await self._start_reconciliation_worker()
            self.state.started_at = time.time()
            self.state.status = RuntimeStatus.RUNNING
            self._engine_task = asyncio.create_task(self._run_loop())
            if self.runtime_mode == "live":
                # P1.1: Start heartbeat loop to detect and recover broker disconnections
                self._heartbeat_task = asyncio.create_task(
                    self._broker_heartbeat_loop(), name=f"heartbeat_{self.bot_instance_id}"
                )
                # P1.2: Start account equity sync loop to detect drift
                self._account_sync_task = asyncio.create_task(
                    self._account_sync_loop(), name=f"acct_sync_{self.bot_instance_id}"
                )
            logger.info("BotRuntime started: %s", self.bot_instance_id)

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            if self.state.status == RuntimeStatus.STOPPED:
                return
            self.state.status = RuntimeStatus.STOPPED
            self.state.stopped_at = time.time()
            # Cancel heartbeat loop first (prevents reconnect races during shutdown)
            heartbeat_task = getattr(self, "_heartbeat_task", None)
            if heartbeat_task and not heartbeat_task.done():
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass
            self._heartbeat_task = None
            # Cancel account equity sync loop
            account_sync_task = getattr(self, "_account_sync_task", None)
            if account_sync_task and not account_sync_task.done():
                account_sync_task.cancel()
                try:
                    await account_sync_task
                except asyncio.CancelledError:
                    pass
            self._account_sync_task = None
            if self._engine_task:
                self._engine_task.cancel()
                try:
                    await self._engine_task
                except asyncio.CancelledError:
                    pass
                self._engine_task = None
            if self._reconciliation_worker is not None:
                try:
                    await self._reconciliation_worker.stop()
                except Exception as exc:
                    logger.warning(
                        "Reconciliation worker stop failed for %s: %s",
                        self.bot_instance_id,
                        exc,
                    )
                self._reconciliation_worker = None
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

    async def reconcile_now(self) -> Dict[str, Any]:
        if self.runtime_mode != "live":
            raise RuntimeError("reconcile_now_supported_for_live_only")
        if self._reconciliation_worker is None:
            raise RuntimeError("reconciliation_worker_not_running")
        result = await self._reconciliation_worker.run_once()
        return result.to_dict()

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
            # P1.2: Market hours gate — block new signals outside trading session.
            # SessionManager is built from strategy_config["session"] and only
            # blocks new order signals; trade management (SL, TP, trailing) continues.
            if self.runtime_mode == "live" and not self._is_trading_session_open():
                self.state.metadata["market_hours_blocked"] = True
                return
            self.state.metadata["market_hours_blocked"] = False

            df = await self._fetch_market_data()
            if df is None or df.empty:
                return
            wave = self._analyse_market(df)
            signal = await self._generate_signal(df, wave)
            if signal and signal.get("direction") in {"BUY", "SELL"}:
                await self._persist_signal(signal)
                trade_signal = self._build_trade_signal(signal, df)
                if self.runtime_mode == "live":
                    # P0: single brain/execution path in live mode (no legacy queue fallback)
                    await self._execute_signal(trade_signal)
                else:
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
        quality_reason = "ok"
        quality_details: Dict[str, Any] = {}
        quality_ok = bool(df is not None and not df.empty)
        if quality_ok:
            try:
                from trading_core.data import MarketDataQualityEngine

                result = MarketDataQualityEngine().evaluate(df)
                quality_ok = bool(result.ok)
                quality_reason = str(result.reason)
                quality_details = dict(result.details)
            except Exception as exc:
                quality_ok = False
                quality_reason = f"quality_engine_failed:{exc}"
        self.state.metadata["market_data_ok"] = bool(quality_ok)
        self.state.metadata["market_data_quality_reason"] = quality_reason
        self.state.metadata["market_data_quality_details"] = quality_details
        if not quality_ok:
            return None
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

    def _is_trading_session_open(self) -> bool:
        """Check whether the current UTC time falls within the configured trading session.

        Uses SessionManager from backend/engine/session_manager when available.
        Defaults to True (allow trading) when the engine cannot be imported or
        when no session config is provided (full 24/7 for crypto / full-week FX).
        """
        try:
            from engine.session_manager import (  # type: ignore[import]
                SessionManager,
                TradingSession,
                DSTMode,
                CustomSessionConfig,
            )
        except ImportError:
            try:
                from trading_core.engines.session_manager import (  # type: ignore[import,no-redef]
                    SessionManager,
                    TradingSession,
                    DSTMode,
                    CustomSessionConfig,
                )
            except ImportError:
                # SessionManager unavailable — fail open (allow trading)
                return True

        session_cfg = (self.strategy_config or {}).get("session", {}) if isinstance(self.strategy_config, dict) else {}
        if not session_cfg:
            return True

        try:
            session_name = str(session_cfg.get("name", "ALL_DAY") or "ALL_DAY").upper()
            session = TradingSession[session_name] if session_name in TradingSession.__members__ else TradingSession.ALL_DAY
            dst_mode_name = str(session_cfg.get("dst_mode", "NO_DST") or "NO_DST").upper()
            dst_mode = DSTMode[dst_mode_name] if dst_mode_name in DSTMode.__members__ else DSTMode.NO_DST
            gmt_offset = float(session_cfg.get("gmt_offset", 0.0) or 0.0)
            custom_cfg = None
            if session == TradingSession.CUSTOM:
                custom_raw = session_cfg.get("custom", {}) or {}
                custom_cfg = CustomSessionConfig(
                    start_hour=int(custom_raw.get("start_hour", 8)),
                    start_minute=int(custom_raw.get("start_minute", 0)),
                    end_hour=int(custom_raw.get("end_hour", 17)),
                    end_minute=int(custom_raw.get("end_minute", 0)),
                )
            mgr = SessionManager(
                session=session,
                dst_mode=dst_mode,
                gmt_offset=gmt_offset,
                custom_config=custom_cfg,
            )
            return bool(mgr.is_trading_time())
        except Exception as exc:
            logger.warning("Session gate check failed for %s: %s — defaulting to open", self.bot_instance_id, exc)
            return True

    async def _generate_signal(self, df, wave):
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

                provider_name = self._resolve_broker_identity()
                if self.runtime_mode == "live" and provider_name == "stub":
                    raise RuntimeError("live_broker_identity_unresolved")

                brain_input = BrainInput(
                    symbol=str(signal["symbol"]),
                    timeframe=getattr(self.broker_provider, "timeframe", "M5"),
                    broker=provider_name,
                    market={
                        "close": entry_price,
                        "wave_state": wave_state,
                        "confidence": signal["confidence"],
                        "broker_connected": bool(getattr(self.broker_provider, "is_connected", False)),
                    },
                    account={"equity": self.state.equity},
                    positions=[],
                    signals=[signal],
                    settings={
                        "runtime_mode": self.runtime_mode,
                        "risk_pct": float(self.risk_config.get("risk_pct", 0.5))
                        if isinstance(self.risk_config, dict)
                        else 0.5,
                    },
                    telemetry={"runtime_mode": self.runtime_mode},
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
                if self.runtime_mode == "live":
                    await self._emit_required_event("brain_cycle", cycle_result.to_dict(), "brain_cycle_persistence_failed")
                else:
                    await self._emit_event("brain_cycle", cycle_result.to_dict())
            except Exception as exc:
                logger.warning("Brain run_cycle failed [%s]: %s", self.bot_instance_id, exc)
                if self.runtime_mode == "live":
                    self.state.error_message = f"brain_run_cycle_failed: {exc}"
                    signal["direction"] = "HOLD"
                    signal["brain_action"] = "BLOCK"
                    signal["brain_reason"] = self.state.error_message

        return signal

    def _resolve_broker_identity(self) -> str:
        provider_name = str(
            getattr(self.broker_provider, "provider_name", "")
            or getattr(self.broker_provider, "mode", "")
            or ""
        ).lower()
        if provider_name in {"", "unknown"}:
            provider_name = self.broker_provider.__class__.__name__.replace("Provider", "").lower()
        if provider_name in {"_asyncpaperadapter", "paperprovider"}:
            return "paper"
        if provider_name in {"ctrader", "mt5", "bybit", "paper"}:
            return provider_name
        return "stub"

    async def _emit_required_event(self, event_type: str, payload: Dict[str, Any], error_code: str) -> None:
        if self._on_event is None:
            raise RuntimeError(f"{error_code}:missing_on_event_hook")
        try:
            await self._on_event(event_type, payload)
        except Exception as exc:
            raise RuntimeError(f"{error_code}:{exc}") from exc

    def _build_trade_signal(self, signal: Dict[str, Any], df):
        from trading_core.engines.signal_coordinator import TradeSignal
        from trading_core.risk import PositionSizingInput, calculate_position_size, pip_size_for_symbol, pip_value_per_lot

        entry_price = float(signal.get("entry_price") or df["close"].iloc[-1])
        atr = float((df["high"].tail(14) - df["low"].tail(14)).mean() or 0.0)
        if atr <= 0:
            atr = entry_price * 0.001

        direction = str(signal.get("direction") or "HOLD").upper()
        default_lot = float(self.risk_config.get("lot_size", 0.01)) if isinstance(self.risk_config, dict) else 0.01
        rr = float(self.strategy_config.get("rr", 2.0)) if isinstance(self.strategy_config, dict) else 2.0
        if direction == "BUY":
            sl = entry_price - atr
            tp = entry_price + atr * rr
        else:
            sl = entry_price + atr
            tp = entry_price - atr * rr

        lot_size = default_lot
        if isinstance(self.risk_config, dict) and bool(self.risk_config.get("use_risk_position_sizing", True)):
            equity = float(self.state.equity or 0.0)
            if equity <= 0:
                equity = float(self.state.balance or 0.0)
            # P0.7: In live mode use "reject" policy — never silently inflate lot size
            # to meet min_lot; block the trade instead so risk intent is honoured.
            rounding_policy = "reject" if self.runtime_mode == "live" else "floor"
            sizing = calculate_position_size(
                PositionSizingInput(
                    equity=equity,
                    risk_pct=float(self.risk_config.get("risk_pct", 0.5) or 0.5),
                    entry_price=entry_price,
                    stop_loss=float(sl),
                    pip_size=pip_size_for_symbol(str(signal.get("symbol", "EURUSD"))),
                    pip_value_per_lot=pip_value_per_lot(str(signal.get("symbol", "EURUSD"))),
                    min_lot=float(self.risk_config.get("min_lot", 0.01) or 0.01),
                    max_lot=float(self.risk_config.get("max_lot", 100.0) or 100.0),
                    lot_step=float(self.risk_config.get("lot_step", 0.01) or 0.01),
                    rounding_policy=rounding_policy,
                )
            )
            if sizing.blocked:
                logger.warning(
                    "Position sizing blocked for signal %s (live mode, policy=reject): %s",
                    signal.get("signal_id", ""),
                    sizing.block_reason,
                )
                return TradeSignal(
                    signal_id=str(signal.get("signal_id")),
                    symbol=str(signal.get("symbol", "EURUSD")),
                    direction="HOLD",
                    entry_price=entry_price,
                    sl=float(sl),
                    tp=float(tp),
                    lot_size=0.0,
                    entry_mode="runtime_auto",
                    priority=5,
                    meta={"block_reason": sizing.block_reason, "blocked": True},
                )
            if sizing.lot > 0:
                lot_size = sizing.lot

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
            meta={
                "wave_state": signal.get("wave_state", ""),
                "confidence": float(signal.get("confidence", 0.0)),
                "brain_cycle_id": str(signal.get("brain_cycle_id", "")),
                "policy_snapshot": self.state.metadata.get("last_brain_cycle", {}).get("policy_snapshot", {}),
            },
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

        # P0.2: idempotency_key is the canonical binding key — must be set FIRST
        idempotency_key = str(signal.signal_id)

        request = OrderRequest(
            symbol=signal.symbol,
            side=signal.direction.lower(),
            volume=float(signal.lot_size),
            order_type="market",
            price=float(signal.entry_price),
            stop_loss=float(signal.sl),
            take_profit=float(signal.tp),
            comment=idempotency_key,
            client_order_id=idempotency_key,
            idempotency_key=idempotency_key,
        )

        try:
            from execution_service.parity_contract import validate_order_contract
        except ImportError:
            validate_order_contract = None

        # ── P0: Pre-execution gate ─────────────────────────────────────
        if self._gate is not None:
            entry = float(signal.entry_price)
            sl = float(signal.sl)
            rr = abs((float(signal.tp) - entry) / (entry - sl)) if abs(entry - sl) > 0 else 0.0
            spread_pips = float(getattr(self.broker_provider, "spread_pips", 0.0))
            quote_id = ""
            quote_timestamp = 0.0
            broker_server_time = 0.0
            instrument_spec_hash = ""
            policy_meta = getattr(signal, "meta", {}) or {}
            policy_snapshot = policy_meta.get("policy_snapshot", {})
            policy_version = str(
                (getattr(signal, "meta", {}) or {}).get("policy_version")
                or (getattr(signal, "meta", {}) or {}).get("policy_version_id")
                or ""
            )
            policy_hash = str(
                policy_meta.get("policy_hash")
                or self.state.metadata.get("policy_hash")
                or _stable_hash_payload(
                    {
                        "policy_version": policy_version,
                        "policy_snapshot": policy_snapshot if isinstance(policy_snapshot, dict) else {},
                    }
                )
            )
            if self.runtime_mode == "live" and not policy_version:
                self.state.error_message = "missing_policy_version"
                return
            if self.runtime_mode == "live":
                # P0.2: Fetch live quote for real spread; fail closed if missing
                get_quote_fn = getattr(self.broker_provider, "get_quote", None)
                if not callable(get_quote_fn):
                    self.state.error_message = "live_quote_fetch_unavailable"
                    return
                try:
                    live_quote = await get_quote_fn(str(signal.symbol))
                except Exception as quote_exc:
                    self.state.error_message = f"live_quote_fetch_failed:{quote_exc}"
                    return
                if not isinstance(live_quote, dict):
                    self.state.error_message = "live_quote_unavailable"
                    return
                if "spread_pips" in live_quote:
                    spread_pips = float(live_quote.get("spread_pips", 0.0) or 0.0)
                elif live_quote.get("bid") is not None and live_quote.get("ask") is not None:
                    bid = float(live_quote.get("bid") or 0.0)
                    ask = float(live_quote.get("ask") or 0.0)
                    if bid <= 0 or ask <= 0 or ask < bid:
                        self.state.error_message = "live_quote_invalid_bid_ask"
                        return
                    pip_size = 0.01 if "JPY" in str(signal.symbol).upper() else 0.0001
                    spread_pips = (ask - bid) / pip_size
                else:
                    self.state.error_message = "live_quote_missing_spread"
                    return
                quote_id = str(
                    live_quote.get("quote_id")
                    or live_quote.get("id")
                    or live_quote.get("tick_id")
                    or f"{signal.symbol}:{int(time.time() * 1000)}"
                )
                quote_timestamp = float(
                    live_quote.get("timestamp")
                    or live_quote.get("ts")
                    or live_quote.get("server_time")
                    or time.time()
                )
                broker_server_time = float(live_quote.get("server_time") or quote_timestamp or time.time())
                if quote_timestamp <= 0:
                    self.state.error_message = "live_quote_timestamp_invalid"
                    return
                account_info_fn = getattr(self.broker_provider, "get_account_info", None)
                if not callable(account_info_fn):
                    self.state.error_message = "broker_account_info_unavailable"
                    return
                try:
                    account = await account_info_fn()
                    equity = float(getattr(account, "equity", 0.0) or 0.0)
                except Exception as exc:
                    self.state.error_message = f"broker_account_info_fetch_failed:{exc}"
                    return
                await self._emit_event(
                    "broker_account_snapshot",
                    {
                        "bot_instance_id": self.bot_instance_id,
                        "broker": str(getattr(self.broker_provider, "provider_name", "unknown")),
                        "account_id": str(getattr(account, "account_id", "") or "") or None,
                        "balance": float(getattr(account, "balance", 0.0) or 0.0),
                        "equity": float(getattr(account, "equity", 0.0) or 0.0),
                        "margin": float(getattr(account, "margin", 0.0) or 0.0),
                        "free_margin": float(getattr(account, "free_margin", 0.0) or 0.0),
                        "margin_level": float(getattr(account, "margin_level", 0.0) or 0.0),
                        "currency": str(getattr(account, "currency", "") or "") or None,
                    },
                )
                if self._refresh_daily_state_from_broker is None:
                    self.state.error_message = "daily_state_refresh_service_unavailable"
                    return

                try:
                    from trading_core.risk import PositionSizingInput, calculate_position_size, pip_size_for_symbol, pip_value_per_lot
                    from trading_core.risk.instrument_spec import InstrumentSpec, get_fallback_spec

                    # P0.2: In live mode, fetch broker instrument spec for native sizing
                    broker_spec_payload = None
                    instrument_spec = None
                    get_spec_fn = getattr(self.broker_provider, "get_instrument_spec", None)
                    if self.runtime_mode == "live" and callable(get_spec_fn):
                        try:
                            broker_spec_payload = await get_spec_fn(str(signal.symbol))
                        except Exception as spec_exc:
                            self.state.error_message = f"instrument_spec_fetch_failed:{spec_exc}"
                            return
                        if broker_spec_payload is None:
                            self.state.error_message = "instrument_spec_missing_live"
                            return
                    if broker_spec_payload:
                        if self.runtime_mode == "live":
                            required_live_spec = {
                                "pip_size",
                                "pip_value_per_lot",
                                "contract_size",
                                "margin_rate",
                                "min_lot",
                                "max_lot",
                                "lot_step",
                            }
                            missing = [k for k in sorted(required_live_spec) if float(broker_spec_payload.get(k) or 0.0) <= 0.0]
                            if missing:
                                self.state.error_message = f"instrument_spec_incomplete_live:{','.join(missing)}"
                                return
                        instrument_spec_hash = _stable_hash_payload(
                            {
                                "symbol": str(signal.symbol),
                                "spec": {
                                    "pip_size": broker_spec_payload.get("pip_size"),
                                    "pip_value_per_lot": broker_spec_payload.get("pip_value_per_lot"),
                                    "contract_size": broker_spec_payload.get("contract_size"),
                                    "margin_rate": broker_spec_payload.get("margin_rate"),
                                    "min_lot": broker_spec_payload.get("min_lot"),
                                    "max_lot": broker_spec_payload.get("max_lot"),
                                    "lot_step": broker_spec_payload.get("lot_step"),
                                },
                            }
                        )
                        instrument_spec = InstrumentSpec(
                            symbol=str(signal.symbol),
                            pip_size=float(broker_spec_payload.get("pip_size") or 0.0),
                            pip_value_per_lot_usd=float(broker_spec_payload.get("pip_value_per_lot") or 0.0),
                            contract_size=float(broker_spec_payload.get("contract_size") or 0.0),
                            margin_rate=float(broker_spec_payload.get("margin_rate") or 0.0),
                            min_lot=float(broker_spec_payload.get("min_lot") or 0.0),
                            max_lot=float(broker_spec_payload.get("max_lot") or 0.0),
                            lot_step=float(broker_spec_payload.get("lot_step") or 0.0),
                        )
                    elif self.runtime_mode != "live":
                        instrument_spec = get_fallback_spec(str(signal.symbol))
                        instrument_spec_hash = _stable_hash_payload(
                            {
                                "symbol": str(signal.symbol),
                                "spec": {
                                    "pip_size": instrument_spec.pip_size,
                                    "pip_value_per_lot_usd": instrument_spec.pip_value_per_lot_usd,
                                    "contract_size": instrument_spec.contract_size,
                                    "margin_rate": instrument_spec.margin_rate,
                                    "min_lot": instrument_spec.min_lot,
                                    "max_lot": instrument_spec.max_lot,
                                    "lot_step": instrument_spec.lot_step,
                                },
                            }
                        )

                    spec_pip_size = instrument_spec.pip_size if instrument_spec else pip_size_for_symbol(str(signal.symbol))
                    spec_pip_value = instrument_spec.pip_value_per_lot(float(signal.entry_price)) if instrument_spec else pip_value_per_lot(str(signal.symbol))
                    spec_min_lot = instrument_spec.min_lot if instrument_spec else float(self.risk_config.get("min_lot", 0.01) or 0.01) if isinstance(self.risk_config, dict) else 0.01
                    spec_max_lot = instrument_spec.max_lot if instrument_spec else float(self.risk_config.get("max_lot", 100.0) or 100.0) if isinstance(self.risk_config, dict) else 100.0
                    spec_lot_step = instrument_spec.lot_step if instrument_spec else float(self.risk_config.get("lot_step", 0.01) or 0.01) if isinstance(self.risk_config, dict) else 0.01

                    # P0.2: Live mode requires SL; block if missing
                    if self.runtime_mode == "live" and (not signal.sl or float(signal.sl) <= 0):
                        self.state.error_message = "stop_loss_required_in_live"
                        return

                    sizing = calculate_position_size(
                        PositionSizingInput(
                            equity=float(equity),
                            risk_pct=float(self.risk_config.get("risk_pct", 0.5)) if isinstance(self.risk_config, dict) else 0.5,
                            entry_price=float(signal.entry_price),
                            stop_loss=float(signal.sl),
                            pip_size=spec_pip_size,
                            pip_value_per_lot=spec_pip_value,
                            min_lot=spec_min_lot,
                            max_lot=spec_max_lot,
                            lot_step=spec_lot_step,
                        )
                    )
                    approved_lot = float(sizing.lot or 0.0)
                    requested_lot = float(signal.lot_size)
                    if approved_lot <= 0:
                        self.state.error_message = "position_sizing_failed"
                        return
                    if requested_lot > approved_lot + 1e-9:
                        self.state.error_message = "position_size_policy_violation"
                        return
                except Exception as exc:
                    self.state.error_message = f"position_sizing_enforcement_failed:{exc}"
                    return

                refreshed = await self._refresh_daily_state_from_broker(equity)
                if refreshed is None:
                    self.state.error_message = "daily_state_refresh_failed"
                    return
                if self._evaluate_daily_profit_lock is not None:
                    lock_result = await self._evaluate_daily_profit_lock(equity)
                    if bool((lock_result or {}).get("locked", False)):
                        self.state.metadata["daily_lock"] = dict(lock_result or {})
                        self.state.error_message = str((lock_result or {}).get("reason") or "daily_locked")
                        if str((lock_result or {}).get("event") or ""):
                            await self._emit_event(
                                str((lock_result or {}).get("event") or "daily_tp_hit"),
                                {
                                    "bot_instance_id": self.bot_instance_id,
                                    "reason": str((lock_result or {}).get("reason") or "daily_take_profit_hit"),
                                    "lock_action": str((lock_result or {}).get("lock_action") or "stop_new_orders"),
                                    "target": float((lock_result or {}).get("target") or 0.0),
                                },
                            )
                        return
            if not instrument_spec_hash:
                instrument_spec_hash = str(self.state.metadata.get("instrument_spec_hash") or "")
            self.state.metadata["policy_hash"] = policy_hash
            self.state.metadata["instrument_spec_hash"] = instrument_spec_hash
            if quote_id:
                self.state.metadata["quote_id"] = quote_id
            if quote_timestamp > 0:
                self.state.metadata["quote_timestamp"] = quote_timestamp
            daily_state = await self._get_daily_state() if self._get_daily_state else None
            if self.runtime_mode == "live" and daily_state is None:
                self.state.error_message = "daily_state_unavailable"
                return
            if self.runtime_mode == "live":
                max_age = float(self.risk_config.get("max_daily_state_age_seconds", 10.0)) if isinstance(self.risk_config, dict) else 10.0
                updated_raw = (daily_state or {}).get("updated_at")
                updated_ts = None
                if isinstance(updated_raw, str):
                    try:
                        updated_ts = float(datetime.fromisoformat(updated_raw).timestamp())
                    except Exception:
                        updated_ts = None
                elif isinstance(updated_raw, datetime):
                    try:
                        updated_ts = float(updated_raw.timestamp())
                    except Exception:
                        updated_ts = None
                elif hasattr(updated_raw, "isoformat"):
                    try:
                        updated_ts = float(datetime.fromisoformat(updated_raw.isoformat()).timestamp())
                    except Exception:
                        updated_ts = None
                if updated_ts is None:
                    self.state.error_message = "daily_state_stale_or_missing_timestamp"
                    return
                age_seconds = max(0.0, time.time() - updated_ts)
                if age_seconds > max_age:
                    self.state.error_message = "daily_state_stale"
                    return
            gate_ctx = {
                "schema_version": "gate_context_v2",
                "provider_mode": str(getattr(self.broker_provider, "mode", "stub")),
                "runtime_mode": self.runtime_mode,
                "broker_connected": bool(getattr(self.broker_provider, "is_connected", False)),
                "symbol": str(signal.symbol),
                "side": str(signal.direction).lower(),
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
                "daily_locked": bool((daily_state or {}).get("locked", False)),
                "daily_lock_reason": str((daily_state or {}).get("reason") or ""),
                "kill_switch": bool(self.state.metadata.get("kill_switch", False)) or bool((daily_state or {}).get("locked", False)),
                "policy_version_approved": True,
                "new_orders_paused": bool(self.state.metadata.get("new_orders_paused", False)),
                "stop_loss": float(signal.sl or 0.0),
                "requested_volume": float(signal.lot_size or 0.0),
                # P0.3: bind symbol/side/account/policy/slippage/starting_equity to hash
                "account_id": str(getattr(account if self.runtime_mode == "live" else None, "account_id", "") or ""),
                "broker_name": str(getattr(self.broker_provider, "provider_name", "")),
                "starting_equity": float(getattr(account if self.runtime_mode == "live" else None, "equity", 0.0) or 0.0),
                "slippage_pips": 0.0,  # updated after live quote if available
                "policy_version": policy_version,
                "idempotency_key": idempotency_key,
                "quote_id": str(quote_id or self.state.metadata.get("quote_id") or ""),
                "quote_timestamp": float(quote_timestamp or self.state.metadata.get("quote_timestamp") or 0.0),
                "broker_server_time": float(broker_server_time or quote_timestamp or time.time()),
                "quote_age_seconds": max(0.0, float((broker_server_time or time.time()) - float(quote_timestamp or time.time()))),
                "instrument_spec_hash": str(instrument_spec_hash or self.state.metadata.get("instrument_spec_hash") or ""),
                "broker_snapshot_hash": _stable_hash_payload(
                    {
                        "symbol": str(signal.symbol),
                        "quote_id": str(quote_id or ""),
                        "quote_timestamp": float(quote_timestamp or 0.0),
                        "broker_server_time": float(broker_server_time or 0.0),
                        "instrument_spec_hash": str(instrument_spec_hash or ""),
                    }
                ),
                "broker_account_snapshot_hash": _stable_hash_payload(
                    {
                        "account_id": str(getattr(account if self.runtime_mode == "live" else None, "account_id", "") or ""),
                        "equity": float(getattr(account if self.runtime_mode == "live" else None, "equity", 0.0) or 0.0),
                        "free_margin": float(getattr(account if self.runtime_mode == "live" else None, "free_margin", 0.0) or 0.0),
                        "margin_level": float(getattr(account if self.runtime_mode == "live" else None, "margin_level", 0.0) or 0.0),
                        "currency": str(getattr(account if self.runtime_mode == "live" else None, "currency", "") or ""),
                    }
                ),
                "risk_context_hash": "",
                "policy_hash": str(policy_hash),
                "policy_version_id": str(policy_version),
                "policy_status": "active",
                "policy_approved_at": float(time.time()),
                "approved_volume": float(signal.lot_size or 0.0),
                "margin_required": 0.0,
                "portfolio_exposure_after_trade": 0.0,
                "unknown_orders_unresolved": False,
            }
            if self.runtime_mode == "live" and self._get_policy_approval_status is not None:
                gate_ctx["policy_version_approved"] = bool(await self._get_policy_approval_status())
            if self.runtime_mode == "live" and self._get_portfolio_risk_snapshot is not None:
                try:
                    portfolio_snapshot = await self._get_portfolio_risk_snapshot()
                    if isinstance(portfolio_snapshot, dict):
                        gate_ctx.update(
                            {
                                "portfolio_daily_loss_pct": float(portfolio_snapshot.get("portfolio_daily_loss_pct", 0.0) or 0.0),
                                "portfolio_open_positions": int(portfolio_snapshot.get("portfolio_open_positions", 0) or 0),
                                "portfolio_kill_switch": bool(portfolio_snapshot.get("portfolio_kill_switch", False)),
                                "workspace_new_orders_paused": bool(portfolio_snapshot.get("workspace_new_orders_paused", False)),
                                "workspace_active_brokers": int(portfolio_snapshot.get("workspace_active_brokers", 0) or 0),
                                "workspace_current_broker_positions": int(portfolio_snapshot.get("workspace_current_broker_positions", 0) or 0),
                                "workspace_broker_concentration_pct": float(portfolio_snapshot.get("workspace_broker_concentration_pct", 0.0) or 0.0),
                            }
                        )
                except Exception as exc:
                    self.state.error_message = f"portfolio_risk_snapshot_failed:{exc}"
                    return

            risk_ctx = None
            if self.runtime_mode == "live":
                try:
                    from trading_core.risk import RiskContextBuilder, estimate_live_margin_required

                    open_positions_fn = getattr(self.broker_provider, "get_open_positions", None)
                    open_positions = await open_positions_fn() if callable(open_positions_fn) else []
                    margin_required = await estimate_live_margin_required(
                        provider=self.broker_provider,
                        symbol=str(signal.symbol),
                        side=str(signal.direction).lower(),
                        volume=float(signal.lot_size),
                        price=float(signal.entry_price),
                    )
                    quote_index: dict[str, dict] = {}
                    if isinstance(locals().get("live_quote"), dict):
                        quote_index[str(signal.symbol).upper()] = dict(locals().get("live_quote") or {})
                    risk_ctx = RiskContextBuilder.build(
                        account_info=account,
                        open_positions=open_positions or [],
                        symbol=str(signal.symbol),
                        entry_price=float(signal.entry_price),
                        stop_loss=float(signal.sl),
                        requested_volume=float(signal.lot_size),
                        risk_pct=float(self.risk_config.get("risk_pct", 0.5)) if isinstance(self.risk_config, dict) else 0.5,
                        instrument_spec=instrument_spec,
                        runtime_mode=self.runtime_mode,
                        broker_margin_required=float(margin_required),
                        quote_snapshots=quote_index,
                        conversion_rates=(self.risk_config.get("conversion_rates") if isinstance(self.risk_config, dict) else None),
                        correlation_buckets=(self.risk_config.get("correlation_buckets") if isinstance(self.risk_config, dict) else None),
                        slippage_pips=float(self.risk_config.get("max_slippage_pips", 0.0) or 0.0) if isinstance(self.risk_config, dict) else 0.0,
                        commission_per_lot=float(self.risk_config.get("commission_per_lot", 0.0) or 0.0) if isinstance(self.risk_config, dict) else 0.0,
                    )
                except Exception as exc:
                    self.state.error_message = f"risk_context_build_failed:{exc}"
                    return

            if risk_ctx is not None:
                gate_ctx.update(
                    {
                        "margin_usage_pct": float(risk_ctx.margin_usage_pct),
                        "free_margin_after_order": float(risk_ctx.free_margin_after_order),
                        "account_exposure_pct": float(risk_ctx.account_exposure_pct),
                        "symbol_exposure_pct": float(risk_ctx.symbol_exposure_pct),
                        "correlated_usd_exposure_pct": float(risk_ctx.correlated_usd_exposure_pct),
                        "max_loss_amount_if_sl_hit": float(risk_ctx.max_loss_amount_if_sl_hit),
                        "approved_volume": float(signal.lot_size or 0.0),
                        "margin_required": float(locals().get("margin_required", 0.0) or 0.0),
                        "portfolio_exposure_after_trade": float(risk_ctx.account_exposure_pct),
                        "risk_context_hash": _stable_hash_payload(dict(getattr(risk_ctx, "__dict__", {}) or {})),
                    }
                )
            gate_result = self._gate.evaluate(gate_ctx)
            try:
                import os
                from trading_core.runtime.pre_execution_gate import hash_gate_context, build_frozen_context_id, sign_gate_context
                gate_ctx_hash = hash_gate_context(gate_ctx)
                frozen_context_id = build_frozen_context_id(gate_ctx)
                context_signature = sign_gate_context(
                    gate_ctx,
                    secret=str(os.getenv("FROZEN_CONTEXT_HMAC_SECRET") or os.getenv("APP_SECRET_KEY") or ""),
                )
                gate_ctx["frozen_context_id"] = str(frozen_context_id)
                gate_ctx["context_signature"] = str(context_signature)
            except Exception:
                gate_ctx_hash = ""
                frozen_context_id = ""
                context_signature = ""
            gate_event = {
                "bot_instance_id": self.bot_instance_id,
                "signal_id": str(signal.signal_id),
                "idempotency_key": idempotency_key,
                "gate_action": gate_result.action,
                "gate_reason": gate_result.reason,
                "gate_details": gate_result.details,
                "gate_context_hash": gate_ctx_hash,
                "frozen_context_id": str(frozen_context_id or ""),
                "context_signature": str(context_signature or ""),
                "gate_context": dict(gate_ctx or {}),
                "runtime_version": str(self.state.metadata.get("runtime_version") or "") or None,
                "brain_cycle_id": str(getattr(signal, "meta", {}).get("brain_cycle_id", "") or ""),
                "broker": str(getattr(self.broker_provider, "provider_name", "")),
                "symbol": str(signal.symbol),
                "side": str(signal.direction).upper(),
                "volume": float(signal.lot_size),
                "request_payload": {
                    "symbol": request.symbol,
                    "side": request.side,
                    "volume": request.volume,
                    "order_type": request.order_type,
                    "price": request.price,
                    "stop_loss": request.stop_loss,
                    "take_profit": request.take_profit,
                },
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
                brain_cycle_id = str(getattr(signal, "meta", {}).get("brain_cycle_id", "") or "")
                try:
                    reserved = await self._reserve_idempotency(
                        idempotency_key,
                        str(signal.signal_id),
                        brain_cycle_id or None,
                    )
                except TypeError:
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
                await self._emit_event(
                    "order_reserved",
                    {
                        "bot_instance_id": self.bot_instance_id,
                        "signal_id": str(signal.signal_id),
                        "idempotency_key": idempotency_key,
                        "brain_cycle_id": str(getattr(signal, "meta", {}).get("brain_cycle_id", "") or ""),
                    },
                )
                if self._set_idempotency_status is not None:
                    brain_cycle_id = str(getattr(signal, "meta", {}).get("brain_cycle_id", "") or "")
                    await self._set_idempotency_status(idempotency_key, "reserved", brain_cycle_id or None)
            elif self.runtime_mode == "live":
                self.state.error_message = "idempotency_service_unavailable"
                return
        # ── end gate ───────────────────────────────────────────────────

        from execution_service.providers.base import ExecutionCommand, PreExecutionContext
        pre_ctx = PreExecutionContext(
            bot_instance_id=self.bot_instance_id,
            runtime_mode=self.runtime_mode,
            provider_mode=str(getattr(self.broker_provider, "mode", "stub")),
            broker_name=str(getattr(self.broker_provider, "provider_name", "")),
            broker_connected=bool(getattr(self.broker_provider, "is_connected", False)),
            market_data_ok=bool(self.state.metadata.get("market_data_ok", False)),
            data_age_seconds=float(self.state.metadata.get("data_age_seconds", 10**9)),
            spread_pips=float(getattr(self.broker_provider, "spread_pips", 0.0)),
            confidence=float(getattr(signal, "meta", {}).get("confidence", 1.0)),
            rr=float(rr),
            open_positions=int(self.state.open_trades),
            daily_profit_amount=float((daily_state or {}).get("daily_profit_amount", self._daily_profit_amount)),
            daily_loss_pct=float((daily_state or {}).get("daily_loss_pct", self._daily_loss_pct)),
            consecutive_losses=int((daily_state or {}).get("consecutive_losses", self._consecutive_losses)),
            daily_locked=bool((daily_state or {}).get("locked", False)),
            kill_switch=bool(self.state.metadata.get("kill_switch", False)),
            idempotency_key=str(idempotency_key),
            brain_cycle_id=str(getattr(signal, "meta", {}).get("brain_cycle_id", "")),
            account_id=str(getattr(locals().get("account", None), "account_id", "") or "") or None,
            order_type=str(request.order_type or "market"),
            entry_price=float(request.price) if request.price is not None else None,
            stop_loss=float(request.stop_loss) if request.stop_loss is not None else None,
            take_profit=float(request.take_profit) if request.take_profit is not None else None,
            policy_version=policy_version,
            policy_snapshot=getattr(signal, "meta", {}).get("policy_snapshot", {}),
            gate_context=dict(gate_ctx or {}),
            context_hash=str(gate_ctx_hash or ""),
            frozen_context_id=str(frozen_context_id or ""),
            context_signature=str(context_signature or ""),
            margin_usage_pct=float((gate_ctx or {}).get("margin_usage_pct", 0.0)),
            free_margin_after_order=float((gate_ctx or {}).get("free_margin_after_order", 0.0)),
            account_exposure_pct=float((gate_ctx or {}).get("account_exposure_pct", 0.0)),
            symbol_exposure_pct=float((gate_ctx or {}).get("symbol_exposure_pct", 0.0)),
            correlated_usd_exposure_pct=float((gate_ctx or {}).get("correlated_usd_exposure_pct", 0.0)),
            portfolio_daily_loss_pct=float((gate_ctx or {}).get("portfolio_daily_loss_pct", 0.0)),
            portfolio_open_positions=int((gate_ctx or {}).get("portfolio_open_positions", 0)),
            portfolio_kill_switch=bool((gate_ctx or {}).get("portfolio_kill_switch", False)),
        )
        command = ExecutionCommand(
            request=request,
            intent={
                "side": signal.direction,
                "symbol": signal.symbol,
                "lot_size": float(signal.lot_size),
            },
            pre_execution_context=pre_ctx,
            idempotency_key=str(idempotency_key),
            brain_cycle_id=str(getattr(signal, "meta", {}).get("brain_cycle_id", "")),
        )
        if validate_order_contract is not None:
            pre_contract = validate_order_contract(
                self.runtime_mode,
                {
                    "signal_id": str(signal.signal_id),
                    "symbol": request.symbol,
                    "side": request.side.upper(),
                    "volume": request.volume,
                    "order_type": request.order_type,
                    "idempotency_key": command.idempotency_key,
                    "brain_cycle_id": command.brain_cycle_id,
                    "pre_execution_context": {"runtime_mode": self.runtime_mode, "provider_mode": pre_ctx.provider_mode},
                },
            )
            self.state.metadata["parity_contract_pre"] = {
                "ok": bool(pre_contract.ok),
                "reason": str(pre_contract.reason),
                "missing": list(pre_contract.missing),
                "mode": self.runtime_mode,
            }
            if self.runtime_mode in {"live", "demo"} and not pre_contract.ok:
                self.state.error_message = f"parity_contract_pre_failed:{pre_contract.reason}"
                return
        if self._set_idempotency_status is not None:
            await self._set_idempotency_status(command.idempotency_key, "broker_submitted", command.brain_cycle_id or None)
        await self._emit_event(
            "order_submitted",
            {
                "bot_instance_id": self.bot_instance_id,
                "signal_id": signal.signal_id,
                "idempotency_key": command.idempotency_key,
                "brain_cycle_id": command.brain_cycle_id,
                "symbol": request.symbol,
                "side": request.side.upper(),
                "volume": request.volume,
            },
        )
        try:
            result = await self._execution_engine.place_order(command)
        except Exception as exc:
            if self._set_idempotency_status is not None:
                await self._set_idempotency_status(command.idempotency_key, "broker_unknown", command.brain_cycle_id or None)
            unknown_payload = {
                "bot_instance_id": self.bot_instance_id,
                "signal_id": signal.signal_id,
                "idempotency_key": command.idempotency_key,
                "brain_cycle_id": command.brain_cycle_id,
                "frozen_context_id": str(getattr(pre_ctx, "frozen_context_id", "") or "") or None,
                "broker_order_id": "",
                "symbol": request.symbol,
                "side": request.side.upper(),
                "order_type": request.order_type,
                "volume": request.volume,
                "price": request.price,
                "status": "unknown",
                "error_message": str(exc),
            }
            await self._emit_event("order_unknown", unknown_payload)
            if self.runtime_mode == "live" and self._reconciliation_worker is not None:
                await self._reconciliation_worker.run_once()
            raise
        order_payload = {
            "bot_instance_id": self.bot_instance_id,
            "signal_id": signal.signal_id,
            "idempotency_key": command.idempotency_key,
            "brain_cycle_id": command.brain_cycle_id,
            "broker": str(getattr(self.broker_provider, "provider_name", "unknown")),
            "client_order_id": str(command.idempotency_key),
            "broker_order_id": str(getattr(result, "broker_order_id", "") or result.order_id or "") or None,
            "broker_position_id": getattr(result, "broker_position_id", None),
            "broker_deal_id": getattr(result, "broker_deal_id", None),
            "symbol": result.symbol,
            "side": result.side.upper(),
            "order_type": request.order_type,
            "volume": result.volume,
            "requested_volume": request.volume,
            "filled_volume": result.volume,
            "price": result.fill_price,
            "avg_fill_price": result.fill_price,
            "commission": result.commission,
            "submit_status": str(getattr(result, "submit_status", "UNKNOWN") or "UNKNOWN"),
            "fill_status": str(getattr(result, "fill_status", "UNKNOWN") or "UNKNOWN"),
            "account_id": str(getattr(result, "account_id", "") or "") or None,
            "server_time": getattr(result, "server_time", None),
            "latency_ms": float(getattr(result, "latency_ms", 0.0) or 0.0),
            "raw_response_hash": str(getattr(result, "raw_response_hash", "") or "") or None,
            "raw_response": dict(getattr(result, "raw_response", {}) or {}),
            "frozen_context_id": str(getattr(pre_ctx, "frozen_context_id", "") or "") or None,
            "status": "filled" if result.success else "rejected",
            "error_message": result.error_message,
        }
        if validate_order_contract is not None:
            post_contract = validate_order_contract(
                self.runtime_mode,
                {
                    "signal_id": str(signal.signal_id),
                    "symbol": result.symbol,
                    "side": result.side.upper(),
                    "volume": result.volume,
                    "order_type": request.order_type,
                    "idempotency_key": command.idempotency_key,
                    "brain_cycle_id": command.brain_cycle_id,
                    "pre_execution_context": {"runtime_mode": self.runtime_mode, "provider_mode": pre_ctx.provider_mode},
                    "success": bool(result.success),
                    "submit_status": str(getattr(result, "submit_status", "UNKNOWN") or "UNKNOWN"),
                    "fill_status": str(getattr(result, "fill_status", "UNKNOWN") or "UNKNOWN"),
                    "broker_order_id": str(result.order_id or "") or None,
                },
            )
            self.state.metadata["parity_contract_post"] = {
                "ok": bool(post_contract.ok),
                "reason": str(post_contract.reason),
                "missing": list(post_contract.missing),
                "mode": self.runtime_mode,
            }
            if self.runtime_mode == "live" and not post_contract.ok:
                self.state.error_message = f"parity_contract_post_failed:{post_contract.reason}"
                await self._emit_event(
                    "order_unknown",
                    {
                        **order_payload,
                        "status": "unknown",
                        "error_message": self.state.error_message,
                    },
                )
                return
        self.state.metadata["last_order"] = order_payload
        if self._on_order:
            await self._safe_hook(self._on_order(order_payload), "on_order")

        if not result.success:
            submit_status = str(getattr(result, "submit_status", "UNKNOWN") or "UNKNOWN").upper()
            fill_status = str(getattr(result, "fill_status", "UNKNOWN") or "UNKNOWN").upper()
            if self.runtime_mode == "live" and (submit_status == "UNKNOWN" or fill_status == "UNKNOWN"):
                if self._set_idempotency_status is not None:
                    await self._set_idempotency_status(command.idempotency_key, "broker_unknown", command.brain_cycle_id or None)
                await self._emit_event(
                    "order_unknown",
                    {
                        **order_payload,
                        "status": "unknown",
                        "error_message": str(result.error_message or "broker_submit_unknown"),
                    },
                )
                if self._reconciliation_worker is not None:
                    await self._reconciliation_worker.run_once()
                return
            if self._set_idempotency_status is not None:
                await self._set_idempotency_status(command.idempotency_key, "rejected", command.brain_cycle_id or None)
            self.state.error_message = result.error_message
            self._consecutive_losses += 1
            await self._emit_event("order_rejected", order_payload)
            return

        # Receipt-grade requirement in live mode: do not open trade without broker ack/fill proof.
        if self.runtime_mode == "live":
            submit_status = str(getattr(result, "submit_status", "UNKNOWN") or "UNKNOWN").upper()
            fill_status = str(getattr(result, "fill_status", "UNKNOWN") or "UNKNOWN").upper()
            if submit_status not in {"ACKED"} or fill_status not in {"FILLED", "PARTIAL"} or float(result.fill_price or 0.0) <= 0:
                if self._set_idempotency_status is not None:
                    await self._set_idempotency_status(command.idempotency_key, "broker_unknown", command.brain_cycle_id or None)
                unknown_payload = {
                    **order_payload,
                    "status": "unknown",
                    "error_message": "execution_receipt_unverified",
                }
                await self._emit_event("order_unknown", unknown_payload)
                if self._reconciliation_worker is not None:
                    await self._reconciliation_worker.run_once()
                return

        if self._set_idempotency_status is not None:
            await self._set_idempotency_status(command.idempotency_key, "filled", command.brain_cycle_id or None)

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
        # P1.5: Slippage tracking — record (expected_price, fill_price, slippage_pips)
        expected_price = float(request.price or result.fill_price or 0.0)
        fill_price = float(result.fill_price or 0.0)
        if expected_price > 0 and fill_price > 0:
            # Use broker instrument spec pip_size if available; fall back to standard FX values.
            # Hardcoded 0.0001/0.01 are standard defaults only — actual spec is preferred.
            pip_size = 0.01 if "JPY" in str(result.symbol).upper() else 0.0001
            spec_fn = getattr(self.broker_provider, "get_instrument_spec", None)
            if callable(spec_fn):
                try:
                    spec = await spec_fn(str(result.symbol))
                    if isinstance(spec, dict) and float(spec.get("pip_size") or 0.0) > 0:
                        pip_size = float(spec["pip_size"])
                except Exception:
                    pass  # fall through to standard default
            slippage_pips = abs(fill_price - expected_price) / pip_size
            slippage_payload = {
                "bot_instance_id": self.bot_instance_id,
                "signal_id": str(signal.signal_id),
                "idempotency_key": idempotency_key,
                "broker_order_id": str(result.order_id or ""),
                "symbol": result.symbol,
                "side": result.side.upper(),
                "expected_price": round(expected_price, 6),
                "fill_price": round(fill_price, 6),
                "slippage_pips": round(slippage_pips, 2),
                "broker": str(getattr(self.broker_provider, "provider_name", "unknown")),
                "runtime_mode": self.runtime_mode,
                "ts": time.time(),
            }
            self.state.metadata["last_slippage"] = slippage_payload
            await self._emit_event("slippage_recorded", slippage_payload)
            if self.runtime_mode == "live":
                max_slippage = float(
                    self.risk_config.get("max_slippage_pips", 5.0)
                    if isinstance(self.risk_config, dict)
                    else 5.0
                )
                if slippage_pips > max_slippage:
                    logger.warning(
                        "Slippage breach for %s: %.2f pips > max %.2f pips (fill=%.5f expected=%.5f)",
                        self.bot_instance_id, slippage_pips, max_slippage, fill_price, expected_price,
                    )
                    await self._emit_event("slippage_breach", {**slippage_payload, "max_slippage_pips": max_slippage})
        self.state.metadata["last_trade"] = trade_payload
        self._known_trade_volumes[result.order_id] = float(result.volume)
        self._known_remaining_volumes[result.order_id] = float(result.volume)
        if self._on_trade:
            await self._safe_hook(self._on_trade(trade_payload), "on_trade")
        await self._emit_event("order_filled", order_payload)
        await self._emit_event("trade_opened", trade_payload)
        await self._emit_event(
            "open_position_verified",
            {
                "bot_instance_id": self.bot_instance_id,
                "signal_id": signal.signal_id,
                "idempotency_key": command.idempotency_key,
                "broker_trade_id": result.order_id,
                "symbol": result.symbol,
            },
        )

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

        if self.runtime_mode == "live":
            # Live manual actions are operator intents and must run through brain + final gate.
            generated = await self._generate_signal(df, wave)
            generated["signal_id"] = signal["signal_id"]
            generated["manual_intent"] = {
                "requested_direction": signal["direction"],
                "requested_confidence": signal["confidence"],
            }
            await self._persist_signal(generated)
            if generated.get("direction") in {"BUY", "SELL"}:
                trade_signal = self._build_trade_signal(generated, df)
                await self._execute_signal(trade_signal)
            return generated

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

    # ── P1.1: Broker heartbeat / reconnection loop ─────────────────────── #

    async def _broker_heartbeat_loop(self) -> None:
        """Periodically ping the broker; attempt reconnection with exponential backoff
        when the connection is lost.  Emits an alert event and pauses the bot
        on disconnect; resumes when re-connection succeeds and LiveReadinessGuard
        approves.
        """
        logger.info("Broker heartbeat loop started: %s", self.bot_instance_id)
        while True:
            if self.state.status == RuntimeStatus.STOPPED:
                break
            await asyncio.sleep(self._heartbeat_interval)
            if self.state.status == RuntimeStatus.STOPPED:
                break

            connected = bool(getattr(self.broker_provider, "is_connected", False))
            if connected:
                # Run health check to detect degraded-but-connected states
                health_fn = getattr(self.broker_provider, "health_check", None)
                if callable(health_fn):
                    try:
                        details = await health_fn()
                        if isinstance(details, dict):
                            status = str(details.get("status", "healthy")).lower()
                            if status in {"auth_failed", "disconnected", "degraded", "error"}:
                                connected = False
                                logger.warning(
                                    "Heartbeat: broker health degraded for %s: %s",
                                    self.bot_instance_id, status,
                                )
                    except Exception as exc:
                        logger.warning("Heartbeat: health_check raised for %s: %s", self.bot_instance_id, exc)
                        connected = False

            if connected:
                self.state.metadata["broker_heartbeat_ok"] = True
                continue

            # --- Broker disconnected: enter reconnect sequence ---
            logger.warning(
                "Heartbeat: broker disconnected for %s — pausing and attempting reconnection",
                self.bot_instance_id,
            )
            if self.state.status == RuntimeStatus.RUNNING:
                self.state.status = RuntimeStatus.PAUSED
            await self._emit_event(
                "broker_disconnected",
                {
                    "bot_instance_id": self.bot_instance_id,
                    "broker": str(getattr(self.broker_provider, "provider_name", "unknown")),
                    "ts": time.time(),
                },
            )
            self.state.metadata["broker_heartbeat_ok"] = False

            reconnected = False
            for backoff in self._heartbeat_backoff_steps:
                if self.state.status == RuntimeStatus.STOPPED:
                    return
                await asyncio.sleep(backoff)
                if self.state.status == RuntimeStatus.STOPPED:
                    return
                connect_fn = getattr(self.broker_provider, "connect", None)
                if callable(connect_fn):
                    try:
                        await connect_fn()
                    except Exception as exc:
                        logger.warning(
                            "Heartbeat: reconnect attempt failed for %s (backoff=%.0fs): %s",
                            self.bot_instance_id, backoff, exc,
                        )
                        continue
                if bool(getattr(self.broker_provider, "is_connected", False)):
                    # Run LiveReadinessGuard before resuming trading
                    try:
                        from apps.api.app.services.live_readiness_guard import LiveReadinessGuard
                    except ImportError:
                        try:
                            from app.services.live_readiness_guard import LiveReadinessGuard  # type: ignore[import,no-redef]
                        except ImportError:
                            LiveReadinessGuard = None  # type: ignore[assignment]
                    readiness_ok = True
                    if LiveReadinessGuard is not None:
                        try:
                            r = await LiveReadinessGuard.check_provider(
                                self.broker_provider, require_live=True
                            )
                            readiness_ok = bool(r.ok)
                            if not readiness_ok:
                                logger.warning(
                                    "Heartbeat: readiness check failed after reconnect for %s: %s",
                                    self.bot_instance_id, r.reason,
                                )
                        except Exception as exc:
                            logger.warning(
                                "Heartbeat: LiveReadinessGuard check failed for %s: %s",
                                self.bot_instance_id, exc,
                            )
                            readiness_ok = False
                    if readiness_ok:
                        reconnected = True
                        break

            if reconnected:
                if self.state.status == RuntimeStatus.PAUSED:
                    self.state.status = RuntimeStatus.RUNNING
                self.state.metadata["broker_heartbeat_ok"] = True
                logger.info(
                    "Heartbeat: broker reconnected and readiness confirmed for %s — resuming",
                    self.bot_instance_id,
                )
                await self._emit_event(
                    "broker_reconnected",
                    {
                        "bot_instance_id": self.bot_instance_id,
                        "broker": str(getattr(self.broker_provider, "provider_name", "unknown")),
                        "ts": time.time(),
                    },
                )
            else:
                # Exhausted retries: escalate to ERROR so operator must intervene
                self.state.status = RuntimeStatus.ERROR
                self.state.error_message = "broker_reconnection_failed"
                self.state.metadata["broker_heartbeat_ok"] = False
                logger.error(
                    "Heartbeat: all reconnect attempts exhausted for %s — runtime set to ERROR",
                    self.bot_instance_id,
                )
                await self._emit_event(
                    "broker_reconnection_failed",
                    {
                        "bot_instance_id": self.bot_instance_id,
                        "broker": str(getattr(self.broker_provider, "provider_name", "unknown")),
                        "ts": time.time(),
                    },
                )
                break

        logger.info("Broker heartbeat loop stopped: %s", self.bot_instance_id)

    # ── P1.2: Account equity sync loop ──────────────────────────────────── #

    async def _account_sync_loop(self) -> None:
        """Periodically fetch live account equity from the broker and detect
        unexpected equity drift (e.g. from manual trades, rollover charges, or
        data-source divergence).

        When the measured drift exceeds ``max_equity_drift_pct`` (from config /
        API settings), the loop emits an ``equity_drift_alert`` event and may
        trigger a daily state refresh so the risk engine re-calibrates from the
        real broker balance.
        """
        logger.info("Account sync loop started: %s", self.bot_instance_id)

        # Allow one full tick interval before the first sync so startup isn't raced.
        await asyncio.sleep(self._account_sync_interval)

        while True:
            if self.state.status == RuntimeStatus.STOPPED:
                break
            await asyncio.sleep(self._account_sync_interval)
            if self.state.status == RuntimeStatus.STOPPED:
                break

            try:
                account_info_fn = getattr(self.broker_provider, "get_account_info", None)
                if not callable(account_info_fn):
                    continue

                account = await account_info_fn()
                broker_equity = float(getattr(account, "equity", 0.0) or 0.0)
                broker_balance = float(getattr(account, "balance", 0.0) or 0.0)

                if broker_equity <= 0:
                    logger.warning(
                        "AccountSync: broker returned equity=0 for %s — skipping",
                        self.bot_instance_id,
                    )
                    continue

                # Record starting equity on first successful sync
                if self._starting_equity <= 0:
                    self._starting_equity = broker_equity

                # Update internal state with live broker values
                self.state.equity = broker_equity
                self.state.balance = broker_balance
                self.state.metadata["broker_equity_synced_at"] = time.time()
                self.state.metadata["broker_equity"] = broker_equity
                self.state.metadata["broker_balance"] = broker_balance

                # Drift detection: compare broker equity vs internal tracked equity
                internal_equity = float(self.state.metadata.get("last_tick_equity", broker_equity))
                if internal_equity > 0 and broker_equity > 0:
                    drift_pct = abs(broker_equity - internal_equity) / internal_equity * 100.0
                    self.state.metadata["equity_drift_pct"] = drift_pct

                    # Read threshold from risk_config, falling back to 1.0%
                    max_drift_pct = 1.0
                    try:
                        from app.core.config import get_settings
                        max_drift_pct = float(get_settings().max_equity_drift_pct or 1.0)
                    except Exception:
                        if isinstance(self.risk_config, dict):
                            max_drift_pct = float(
                                self.risk_config.get("max_equity_drift_pct", 1.0) or 1.0
                            )

                    if drift_pct > max_drift_pct:
                        logger.warning(
                            "AccountSync: equity drift %.2f%% > threshold %.2f%% for %s "
                            "(broker=%.2f, internal=%.2f)",
                            drift_pct,
                            max_drift_pct,
                            self.bot_instance_id,
                            broker_equity,
                            internal_equity,
                        )
                        await self._emit_event(
                            "equity_drift_alert",
                            {
                                "bot_instance_id": self.bot_instance_id,
                                "broker_equity": broker_equity,
                                "internal_equity": internal_equity,
                                "drift_pct": drift_pct,
                                "max_drift_pct": max_drift_pct,
                                "ts": time.time(),
                            },
                        )
                        # Trigger daily state refresh so risk engine re-calibrates
                        if self._refresh_daily_state_from_broker:
                            try:
                                await self._refresh_daily_state_from_broker(broker_equity)
                            except Exception as exc:
                                logger.warning(
                                    "AccountSync: daily state refresh failed for %s: %s",
                                    self.bot_instance_id,
                                    exc,
                                )

                # Emit Prometheus metric if available
                try:
                    from app.core.metrics import ACCOUNT_EQUITY_GAUGE, EQUITY_DRIFT_GAUGE
                    broker_id = str(getattr(self.broker_provider, "provider_name", "unknown"))
                    ACCOUNT_EQUITY_GAUGE.labels(
                        bot_id=self.bot_instance_id, provider=broker_id
                    ).set(broker_equity)
                    drift = float(self.state.metadata.get("equity_drift_pct", 0.0))
                    EQUITY_DRIFT_GAUGE.labels(
                        bot_id=self.bot_instance_id, provider=broker_id
                    ).set(drift)
                except Exception:
                    pass

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning(
                    "AccountSync: error for %s: %s",
                    self.bot_instance_id,
                    exc,
                )

        logger.info("Account sync loop stopped: %s", self.bot_instance_id)


        if self._reconciliation_worker is not None:
            return
        missing_hooks = []
        if not self._get_db_open_trades:
            missing_hooks.append("get_db_open_trades")
        if not self._close_db_trade:
            missing_hooks.append("close_db_trade")
        if not self._on_reconciliation_result:
            missing_hooks.append("on_reconciliation_result")
        if not self._on_reconciliation_incident:
            missing_hooks.append("on_reconciliation_incident")
        if missing_hooks:
            raise RuntimeError(f"missing_reconciliation_hooks:{','.join(missing_hooks)}")
        try:
            from execution_service.reconciliation_worker import ReconciliationWorker
        except ImportError as exc:
            raise RuntimeError(f"reconciliation_worker_unavailable:{exc}") from exc

        async def _on_result(payload: Dict[str, Any]) -> None:
            if self._on_reconciliation_result:
                await self._safe_hook(self._on_reconciliation_result(payload), "on_reconciliation_result")
            await self._emit_event("reconciliation_result", payload)

        async def _on_incident(payload: Dict[str, Any]) -> None:
            if self._on_reconciliation_incident:
                await self._safe_hook(self._on_reconciliation_incident(payload), "on_reconciliation_incident")
            # escalation: fail-closed until operator resolves
            self.state.metadata["kill_switch"] = True
            self.state.status = RuntimeStatus.ERROR
            self.state.error_message = str(payload.get("title") or "reconciliation_incident")
            await self._emit_event("reconciliation_incident", payload)

        self._reconciliation_worker = ReconciliationWorker(
            bot_instance_id=self.bot_instance_id,
            provider=self.broker_provider,
            get_db_open_trades=self._get_db_open_trades,
            on_close_trade=self._close_db_trade,
            on_result=_on_result,
            on_incident=_on_incident,
            interval_seconds=float(self.risk_config.get("reconciliation_interval_seconds", 10.0)) if isinstance(self.risk_config, dict) else 10.0,
            max_mismatch_rounds=int(self.risk_config.get("reconciliation_max_mismatch_rounds", 3)) if isinstance(self.risk_config, dict) else 3,
            get_unknown_order_attempts=self._get_unknown_order_attempts,
            on_unknown_resolved=self._on_unknown_order_resolved,
        )
        await self._reconciliation_worker.start()
        first_result = await self._reconciliation_worker.run_once()
        if str(first_result.status).lower() not in {"ok", "repaired"}:
            await self._reconciliation_worker.stop()
            self._reconciliation_worker = None
            raise RuntimeError(f"reconciliation_first_pass_failed:{first_result.status}")

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
