"""
Robot Forex — FastAPI Backend
Endpoints + WebSocket streaming + background RobotEngine.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Set

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from database import (
    SessionLocal,
    create_tables,
    get_db,
    get_all_trades,
    get_trade_count,
    load_settings,
    save_settings,
    save_trade,
)
from engine import (
    WaveDetector, WaveState,
    SignalCoordinator, CoordinatorState, SignalAuthority,
    RiskManager, LotMode,
    EntryLogic, EntryMode, SLMode, TPMode,
    TradeManager,
    SessionManager, TradingSession,
    MockDataProvider,
    CTraderDataProvider, BrokerStatus,
    AutoPilot,
    RetracementEngine,
    DecisionEngine, DecisionAction,
    CapitalManager,
    CandleLibrary,
    LLMOrchestrator,
    WarmUpPipeline, WarmUpReport,
    EvolutionaryEngine, EvolutionResult,
    MetaLearningEngine, MetaLearningResult, GeneImportance,
    CausalStrategyEngine, CausalIntelligenceResult,
    UtilityConfig, UtilityOptimizationEngine, UtilityOptimizationResult,
    EcosystemConfig, GameTheoryEngine, GameTheoryResult,
    SovereignPolicy, SovereignOversightEngine, SovereignOversightResult,
    SovereignMode, ObjectiveLevel, NetworkDominanceScore,
    EnterpriseConfig, AutonomousEnterpriseEngine, EnterpriseCycle,
    EnterpriseLifecycle,
)
from engine.signal_coordinator import TradeSignal as CoordSignal
from engine.risk_manager import RiskConfig, MartingaleConfig
from engine.trade_manager import PartialCloseConfig, TrailingConfig, GridConfig, BreakEvenConfig, TimeBasedExitConfig
from engine.session_manager import DSTMode
from models.schemas import (
    AutoPilotStatusSchema,
    AutoPilotLastDecisionSchema,
    AutoPilotCandidateSchema,
    RetracementStatusSchema,
    SupportResistanceLevelSchema,
    MarketRegimeSchema,
    SegmentStatsSchema,
    DecisionContextSchema,
    DecisionEngineStatusSchema,
    BrokerStatusSchema,
    CandleSchema,
    PaginatedTrades,
    QueueStatusSchema,
    RiskMetricsSchema,
    RobotSettings,
    RobotStatusSchema,
    TradeRecordSchema,
    WaveAnalysisSchema,
    PerformanceDashboardSchema,
    PreTradeConsultationSchema,
    PatternSummarySchema,
    TradeFingerprintSchema,
    DailyLockStatusSchema,
    CapitalProfileSchema,
    CandleLibraryStatusSchema,
    LLMStatusSchema,
)

import os

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


def _create_data_provider(symbol: str = "EURUSD", timeframe: str = "M5"):
    """
    Factory: trả về CTraderDataProvider nếu có đủ env vars,
    ngược lại fallback về MockDataProvider.
    """
    has_credentials = bool(
        os.environ.get("CTRADER_CLIENT_ID")
        and os.environ.get("CTRADER_CLIENT_SECRET")
        and os.environ.get("CTRADER_ACCESS_TOKEN")
    )
    if has_credentials:
        try:
            provider = CTraderDataProvider(symbol=symbol, timeframe=timeframe)
            logger.info("DataProvider: sử dụng CTraderDataProvider (live=%s)", provider.is_live)
            return provider
        except Exception as exc:
            logger.warning(
                "Không thể khởi động CTraderDataProvider (%s) — fallback sang Mock.", exc
            )
    logger.warning(
        "DataProvider: CTRADER_CLIENT_ID/SECRET/ACCESS_TOKEN chưa được cấu hình → "
        "sử dụng MockDataProvider (chỉ dùng để test)."
    )
    return MockDataProvider(symbol=symbol)

# ── Shared application state ───────────────────────────────────────────── #

class AppState:
    def __init__(self) -> None:
        self.settings: RobotSettings = RobotSettings()
        self.robot_running: bool = False
        self.start_time: float = 0.0
        self.balance: float = 10_000.0
        self.equity: float = 10_000.0

        self.data_provider = _create_data_provider(
            symbol=self.settings.symbol, timeframe=self.settings.timeframe
        )
        self.wave_detector: WaveDetector = WaveDetector()
        self.coordinator: SignalCoordinator = SignalCoordinator()
        self.risk_manager: RiskManager = RiskManager()
        self.entry_logic: EntryLogic = EntryLogic()
        self.trade_manager: TradeManager = TradeManager()
        self.session_manager: SessionManager = SessionManager()
        self.auto_pilot: AutoPilot = AutoPilot()
        self.retracement_engine: RetracementEngine = RetracementEngine()
        self.decision_engine: DecisionEngine = DecisionEngine()
        self.capital_manager: CapitalManager = CapitalManager()
        self.candle_library: CandleLibrary = CandleLibrary(
            symbol=self.settings.symbol,
            timeframe=self.settings.timeframe,
        )
        self.llm: LLMOrchestrator = LLMOrchestrator()

        # Warm-up pipeline — pre-warms ML models at startup so they don't
        # need to wait for N real trades before becoming effective.
        self.warmup_pipeline: WarmUpPipeline = WarmUpPipeline(
            decision_engine=self.decision_engine,
            wave_detector=self.wave_detector,
        )
        self.warmup_report: Optional[WarmUpReport] = None

        # Evolutionary engine — self-play strategy optimizer.
        # Runs a population of trading agents through synthetic markets,
        # evolves them, and applies the best genome to the live system.
        self.evolution_engine: EvolutionaryEngine = EvolutionaryEngine()
        self.evolution_result: Optional[EvolutionResult] = None

        # Meta-learning engine — strategy genetics system.
        # Learns WHY winners win, accumulates gene knowledge, and breeds
        # smarter strategies across multiple evolution loops.
        self.meta_engine: MetaLearningEngine = MetaLearningEngine()
        self.meta_result: Optional[MetaLearningResult] = None

        # Causal strategy engine — world model + causal strategic intelligence.
        # Learns which genes CAUSE wins (not just correlate), detects spurious
        # correlations, tests regime robustness, and infers counterfactual strategies.
        self.causal_engine: CausalStrategyEngine = CausalStrategyEngine()
        self.causal_result: Optional[CausalIntelligenceResult] = None

        # Utility optimization engine — decision theory + rational strategic agent.
        # Optimises multi-dimensional utility (growth, trust, stability, speed,
        # dominance) and applies Kelly criterion for rational lot sizing.
        self.utility_engine: UtilityOptimizationEngine = UtilityOptimizationEngine()
        self.utility_result: Optional[UtilityOptimizationResult] = None

        # Game theory engine — multi-agent ecosystem + Nash equilibrium.
        # Optimises in environment with opponents, market maker algorithms,
        # and market impact. Finds best-response strategy and Nash equilibrium.
        self.ecosystem_engine: GameTheoryEngine = GameTheoryEngine()
        self.ecosystem_result: Optional[GameTheoryResult] = None

        # Sovereign oversight engine — network-level governor of the full
        # intelligence stack (layer 7).  Sets objectives, allocates attention
        # budgets, and issues governance directives (SCALE_UP/THROTTLE/KILL).
        self.sovereign_engine: SovereignOversightEngine = SovereignOversightEngine()
        self.sovereign_result: Optional[SovereignOversightResult] = None

        # Autonomous enterprise engine — self-evolving autonomous entity (layer 8).
        # Orchestrates all 7 lower layers, self-allocates resources, self-evolves
        # its governance policy, and operates as an independent enterprise.
        self.enterprise_engine: AutonomousEnterpriseEngine = AutonomousEnterpriseEngine()
        self.enterprise_task:   Optional[asyncio.Task] = None

        # Maps trade_id → {mode, wave_state, retrace_zone, initial_risk}
        # populated at open, consumed at close for DecisionEngine.record_outcome()
        self._trade_context: Dict[str, Dict[str, Any]] = {}

        self._ws_clients: Set[WebSocket] = set()
        self._engine_task: Optional[asyncio.Task] = None

    def rebuild_components(self) -> None:
        s = self.settings

        # ── Capital profile: auto-tune risk params by balance ────────────── #
        if s.capital_profile != "CUSTOM":
            tuned = self.capital_manager.apply(
                s.model_dump(), self.balance, s.capital_profile
            )
            # Apply capital-profile overrides using Pydantic's model_copy to
            # keep type safety and validation while patching in-memory only
            # (does not persist to DB unless user saves settings explicitly).
            profile_overrides = {
                field: tuned[field]
                for field in (
                    "lot_mode", "lot_value", "min_lot", "max_lot",
                    "max_daily_dd_pct", "max_overall_dd_pct", "max_trades_at_time",
                    "daily_profit_target", "daily_loss_limit",
                )
                if field in tuned
            }
            if profile_overrides:
                self.settings = s.model_copy(update=profile_overrides)
                s = self.settings

        # Chỉ tạo lại data_provider nếu symbol/timeframe thay đổi
        cur_sym = getattr(self.data_provider, "symbol", "")
        cur_tf  = getattr(self.data_provider, "timeframe", "")
        if cur_sym != s.symbol or cur_tf != s.timeframe:
            self.data_provider = _create_data_provider(
                symbol=s.symbol, timeframe=s.timeframe
            )
            # Reset candle library for new symbol/timeframe
            self.candle_library = CandleLibrary(
                symbol=s.symbol, timeframe=s.timeframe
            )
        self.wave_detector = WaveDetector(
            htf_ema_fast=s.htf_ema_fast,
            htf_ema_slow=s.htf_ema_slow,
            ltf_ema_fast=s.ltf_ema_fast,
            ltf_ema_slow=s.ltf_ema_slow,
            sideways_atr_mult=s.sideways_atr_mult,
            sideways_candles=s.sideways_candles,
        )
        self.coordinator = SignalCoordinator(
            max_queue_size=s.max_queue_size,
            max_concurrent_trades=s.max_trades_at_time,
            cooldown_minutes=s.cooldown_minutes,
            signal_expiry_seconds=s.signal_expiry_seconds,
        )
        self.risk_manager = RiskManager(
            config=RiskConfig(
                lot_mode=LotMode(s.lot_mode),
                lot_value=s.lot_value,
                min_lot=s.min_lot,
                max_lot=s.max_lot,
                max_account_equity=s.max_account_equity,
                max_daily_dd_pct=s.max_daily_dd_pct,
                max_overall_dd_pct=s.max_overall_dd_pct,
                pip_value_per_lot=s.pip_value_per_lot,
                daily_profit_target=s.daily_profit_target,
                daily_loss_limit=s.daily_loss_limit,
            ),
            martingale=MartingaleConfig(
                enabled=s.martingale.enabled,
                multiplier=s.martingale.multiplier,
                max_steps=s.martingale.max_steps,
            ),
        )
        self.entry_logic = EntryLogic(
            sl_mode=SLMode(s.sl_mode),
            sl_value=s.sl_value,
            tp_mode=TPMode(s.tp_mode),
            tp_value=s.tp_value,
            entry_mode=EntryMode(s.entry_mode),
            retrace_atr_mult=s.retrace_atr_mult,
            min_body_atr=s.min_body_atr,
            retest_level_x=s.retest_level_x,
        )
        self.trade_manager = TradeManager(
            partial_config=PartialCloseConfig(
                enabled=s.partial_close.enabled,
                trigger_pct=s.partial_close.trigger_pct,
                close_pct=s.partial_close.close_pct,
                move_sl_to_be=s.partial_close.move_sl_to_be,
            ),
            trailing_config=TrailingConfig(
                enabled=s.trailing.enabled,
                mode=s.trailing.mode,
                trigger_pct=s.trailing.trigger_pct,
                trail_pct=s.trailing.trail_pct,
            ),
            grid_config=GridConfig(
                enabled=s.grid.enabled,
                levels=s.grid.levels,
                distance_pips=s.grid.distance_pips,
                distance_multiplier=s.grid.distance_multiplier,
                volume_multiplier=s.grid.volume_multiplier,
                max_grid_lot=s.grid.max_grid_lot,
            ),
            break_even_config=BreakEvenConfig(
                enabled=s.break_even.enabled,
                trigger_pips=s.break_even.trigger_pips,
                offset_pips=s.break_even.offset_pips,
            ),
            time_exit_config=TimeBasedExitConfig(
                enabled=s.time_based_exit.enabled,
                max_duration_minutes=s.time_based_exit.max_duration_minutes,
                min_profit_pips=s.time_based_exit.min_profit_pips,
            ),
            pip_value=s.pip_value_per_lot,
        )
        self.session_manager = SessionManager(
            session=TradingSession(s.session),
            dst_mode=DSTMode(s.dst_mode),
            gmt_offset=s.gmt_offset,
        )
        self.auto_pilot = AutoPilot(
            sl_mode=SLMode(s.sl_mode),
            sl_value=s.sl_value,
            tp_mode=TPMode(s.tp_mode),
            tp_value=s.tp_value,
            retrace_atr_mult=s.retrace_atr_mult,
            min_body_atr=s.min_body_atr,
            retest_level_x=s.retest_level_x,
            entry_cooldown_secs=s.entry_cooldown_secs,
            min_atr_ratio=s.min_atr_ratio,
            allow_subwave_retrace=s.allow_subwave_retrace,
        )
        # RetracementEngine — created inside AutoPilot and aliased here for
        # direct access from endpoints. Both references point to the same object;
        # AutoPilot is the sole owner and caller of .measure().
        self.retracement_engine = self.auto_pilot.retracement_engine
        # DecisionEngine preserves its PerformanceTracker across rebuilds
        # (settings change should not erase accumulated learning).
        self.decision_engine._base_min_score_update(
            float(getattr(self, "_base_min_score", 0.25))
        )
        self.coordinator.set_execute_callback(self._on_signal_execute)
        # Keep warmup_pipeline pointing to the (potentially new) wave_detector
        self.warmup_pipeline = WarmUpPipeline(
            decision_engine=self.decision_engine,
            wave_detector=self.wave_detector,
        )

    async def _on_signal_execute(self, signal: CoordSignal) -> None:
        """Called by coordinator when a signal is approved for execution."""
        trade = self.trade_manager.open_trade(
            symbol=signal.symbol,
            direction=signal.direction,
            entry_price=signal.entry_price,
            sl=signal.sl,
            tp=signal.tp,
            lot_size=signal.lot_size,
            entry_mode=signal.entry_mode,
        )

        # Store trade context for DecisionEngine.record_outcome() on close
        wa = self.wave_detector.last_analysis
        rm = self.retracement_engine.last_measure
        initial_risk = abs(signal.entry_price - signal.sl)
        self._trade_context[trade.trade_id] = {
            "mode":         signal.entry_mode,
            "wave_state":   wa.main_wave.value if wa else "SIDEWAYS",
            "retrace_zone": rm.zone.value if rm else "NOT_RETRACING",
            "initial_risk": initial_risk,
            "atr":          float(signal.atr) if signal.atr else 0.0,
            "entry_price":  signal.entry_price,
        }
        # Persist to DB
        db = SessionLocal()
        try:
            save_trade(db, {
                "trade_id": trade.trade_id,
                "symbol": trade.symbol,
                "direction": trade.direction,
                "lot_size": trade.lot_size,
                "entry_price": trade.entry_price,
                "sl": trade.sl,
                "tp": trade.tp,
                "entry_mode": trade.entry_mode,
                "open_time": trade.open_time,
                "close_time": None,
                "close_price": None,
                "pnl": 0.0,
                "status": "OPEN",
                "remaining_lots": trade.remaining_lots,
                "be_moved": False,
                "grid_level": 0,
                "comment": "",
                "meta": {},
            })
        finally:
            db.close()

        await self.broadcast({"event": "trade_opened", "trade": {
            "trade_id": trade.trade_id,
            "symbol": trade.symbol,
            "direction": trade.direction,
            "entry_price": trade.entry_price,
            "lot_size": trade.lot_size,
        }})

    async def broadcast(self, data: Dict[str, Any]) -> None:
        dead = set()
        for ws in self._ws_clients:
            try:
                await ws.send_json(data)
            except Exception:
                dead.add(ws)
        self._ws_clients -= dead


app_state = AppState()


# ── Lifespan ───────────────────────────────────────────────────────────── #

@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()
    # Load persisted settings
    db = SessionLocal()
    try:
        stored = load_settings(db)
        if stored:
            app_state.settings = RobotSettings(**stored)
            logger.info("Settings loaded from DB")
    finally:
        db.close()
    app_state.rebuild_components()

    # ── ML Warm-up: pre-train models with synthetic data before live trading ─ #
    # This eliminates the cold-start period where models have no data to learn
    # from. Runs synchronously (fast — < 2 seconds) before robot starts.
    try:
        app_state.warmup_report = app_state.warmup_pipeline.run()
        logger.info(
            "WarmUp: done — lstm=%d, outcomes=%d, ensemble=%d, "
            "lstm_ready=%s, ensemble_ready=%s",
            app_state.warmup_report.lstm_samples_injected,
            app_state.warmup_report.outcome_samples_injected,
            app_state.warmup_report.ensemble_samples_injected,
            app_state.warmup_report.lstm_ready,
            app_state.warmup_report.ensemble_ready,
        )
    except Exception as _wu_exc:
        logger.warning("WarmUp failed (non-fatal): %s", _wu_exc)
        # Store a failure report so /api/warmup/status shows the error
        app_state.warmup_report = WarmUpReport(errors=[str(_wu_exc)])
    # ──────────────────────────────────────────────────────────────────────── #

    # ── AUTO-START: Robot tự vận hành ngay khi khởi động ──────────────── #
    # Hệ thống tự quyết định và bắt đầu trading ngay khi server khởi động.
    # Không cần gọi /api/robot/start thủ công.
    # Để tắt tính năng này, gọi POST /api/robot/stop sau khi server khởi động.
    app_state.robot_running = True
    app_state.start_time = time.time()
    app_state.coordinator.start()
    app_state._engine_task = asyncio.create_task(_engine.run())
    logger.info("AutoPilot: robot đã tự khởi động — đang vận hành tự động.")
    # ──────────────────────────────────────────────────────────────────── #

    yield
    # Shutdown
    if app_state._engine_task:
        app_state._engine_task.cancel()


app = FastAPI(title="Robot Forex API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Robot Engine (background task) ────────────────────────────────────────#

class RobotEngine:
    """
    Async background engine — tự vận hành hoàn toàn.

    Mỗi tick (interval tự điều chỉnh):
      1. Quản lý lệnh đang mở (SL/TP/trailing/partial) — LUÔN ưu tiên trước.
      2. Tính equity thực tế, kiểm tra drawdown.
      3. AutoPilot scan tất cả EntryModes, chọn setup tốt nhất.
      4. Nếu setup đủ điểm → submit signal với dynamic priority.
      5. Coordinator xử lý queue, execute lệnh tốt nhất.
      6. Broadcast live update qua WebSocket.
    """

    def __init__(self, state: AppState) -> None:
        self.state = state
        self._tick_interval = 5.0   # tự điều chỉnh bởi AutoPilot
        self._daily_trades = 0
        self._last_day: Optional[int] = None

    async def run(self) -> None:
        logger.info("RobotEngine (AutoPilot) started")
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                logger.info("RobotEngine stopped")
                break
            except Exception as exc:
                logger.error("Engine tick error: %s", exc, exc_info=True)
            # Dùng tick interval do AutoPilot tự điều chỉnh
            self._tick_interval = self.state.auto_pilot.get_current_tick_interval()
            await asyncio.sleep(self._tick_interval)

    async def _tick(self) -> None:
        if not self.state.robot_running:
            return

        s = self.state.settings

        # Advance mock data
        self.state.data_provider.advance()
        df = self.state.data_provider.get_candles(limit=200)
        if len(df) < 60:
            return

        # ── Feed realtime candles to library ──────────────────────────── #
        self.state.candle_library.update(df)

        # Daily reset check
        today = int(time.time() // 86400)
        if today != self._last_day:
            self._last_day = today
            self._daily_trades = 0
            self.state.risk_manager.reset_daily(self.state.balance)

        # ── Daily lock auto-stop ───────────────────────────────────────── #
        # If a daily profit/loss lock fires, stop the robot automatically.
        # The user must manually call /api/robot/reset_daily_lock to resume.
        if self.state.risk_manager.daily_locked and self.state.robot_running:
            logger.warning(
                "RobotEngine: daily lock triggered (%s) — auto-stopping robot",
                self.state.risk_manager.lock_reason,
            )
            self.state.robot_running = False
            self.state.coordinator.stop()
            await self.state.broadcast({
                "event":  "daily_lock",
                "reason": self.state.risk_manager.lock_reason,
                "timestamp": time.time(),
            })
            return

        # Wave analysis
        wave_analysis = self.state.wave_detector.analyse(df)

        # ATR
        atr = self.state.data_provider.calculate_atr(df, s.atr_period)

        # Risk update
        open_pnl = sum(
            t.calculate_pnl(df["close"].iloc[-1])
            for t in self.state.trade_manager.get_open_trades()
        )
        self.state.equity = self.state.balance + open_pnl
        self.state.risk_manager.update_equity(self.state.balance, self.state.equity)

        # Update open trades (check SL/TP/trailing/partial/BE/time-exit)
        current_price = float(df["close"].iloc[-1])
        candle_high = float(df["high"].iloc[-1])
        candle_low  = float(df["low"].iloc[-1])
        closed_this_tick = []
        for trade in list(self.state.trade_manager.get_open_trades()):
            actions = self.state.trade_manager.update_trade(
                trade.trade_id, current_price, atr,
                candle_high=candle_high,
                candle_low=candle_low,
            )
            if "closed" in actions:
                # trade is mutated in-place by _close_trade — reuse the reference
                # directly instead of searching the entire closed_trades list (O(n²))
                closed_this_tick.append(trade)

        # Persist closed trades, update balance, record outcome for learning
        if closed_this_tick:
            db = SessionLocal()
            try:
                for ct in closed_this_tick:
                    save_trade(db, {
                        "trade_id": ct.trade_id,
                        "symbol": ct.symbol,
                        "direction": ct.direction,
                        "lot_size": ct.lot_size,
                        "entry_price": ct.entry_price,
                        "sl": ct.sl,
                        "tp": ct.tp,
                        "entry_mode": ct.entry_mode,
                        "open_time": ct.open_time,
                        "close_time": ct.close_time,
                        "close_price": ct.close_price,
                        "pnl": ct.pnl,
                        "status": "CLOSED",
                        "remaining_lots": ct.remaining_lots,
                        "be_moved": ct.be_moved,
                        "grid_level": ct.grid_level,
                        "comment": ct.comment,
                        "meta": {},
                    }, commit=False)  # defer commit; single flush below
                    self.state.balance += ct.pnl
                    self.state.risk_manager.on_trade_closed(ct.pnl)
                    self.state.coordinator.on_trade_closed(ct.pnl)

                    # ── Tự học: feed outcome to DecisionEngine ─────── #
                    ctx = self.state._trade_context.pop(ct.trade_id, {})
                    self.state.decision_engine.record_outcome(
                        mode=ctx.get("mode", ct.entry_mode),
                        wave_state=ctx.get("wave_state", "SIDEWAYS"),
                        direction=ct.direction,
                        retrace_zone=ctx.get("retrace_zone", "NOT_RETRACING"),
                        pnl=ct.pnl,
                        initial_risk=ctx.get("initial_risk", 0.0),
                        atr=ctx.get("atr", 0.0),
                        price=ctx.get("entry_price", 0.0),
                    )

                    # ── Add to LLM knowledge base ──────────────────── #
                    self.state.llm.add_knowledge(
                        text=(
                            f"Trade closed: {ct.direction} {ct.symbol} "
                            f"mode={ct.entry_mode} pnl={ct.pnl:.2f} "
                            f"wave={ctx.get('wave_state', 'UNKNOWN')}"
                        ),
                        metadata={"trade_id": ct.trade_id, "pnl": ct.pnl},
                    )
                # Single commit for all trades closed this tick
                db.commit()
            finally:
                db.close()

        # ── Decision Engine: tự quyết định action + tự dự đoán ──────── #
        open_count = len(self.state.trade_manager.get_open_trades())
        decision_ctx = self.state.decision_engine.decide(
            df=df,
            wave_analysis=wave_analysis,
            atr=atr,
            open_trades_count=open_count,
        )

        # Check if we can open new trade
        daily_limit = s.max_trades_daily
        coordinator_state = self.state.coordinator.state

        can_enter = (
            self._daily_trades < daily_limit
            and open_count < s.max_trades_at_time
            and self.state.risk_manager.is_trading_allowed(self.state.equity)
            and coordinator_state
            not in (CoordinatorState.IDLE, CoordinatorState.COOLDOWN, CoordinatorState.RESTRICTED)
            and self.state.session_manager.is_trading_time()
            # DecisionEngine gates: HOLD and FORCE_PAUSE block new entries
            and decision_ctx.action not in (
                DecisionAction.HOLD, DecisionAction.FORCE_PAUSE
            )
        )

        if can_enter:
            # Check spread filter
            spread = self.state.data_provider.get_spread_points()
            self.state.risk_manager.update_spread(s.symbol, spread)
            spread_ok = self.state.risk_manager.check_spread(s.symbol, s.max_spread)

            if spread_ok:
                await self._autopilot_generate_signal(
                    df, wave_analysis, atr, current_price, decision_ctx
                )

        # Broadcast live update (thêm autopilot + retracement + decision info)
        ap_dec = self.state.auto_pilot.last_decision
        rm = self.state.retracement_engine.last_measure
        de_ctx = self.state.decision_engine.last_context
        await self.state.broadcast({
            "event": "tick",
            "wave": wave_analysis.main_wave,
            "sub_wave": wave_analysis.sub_wave,
            "confidence": wave_analysis.confidence,
            "price": current_price,
            "equity": self.state.equity,
            "balance": self.state.balance,
            "open_trades": open_count,
            "coordinator_state": self.state.coordinator.state.value,
            "timestamp": time.time(),
            "autopilot": {
                "tick_interval": self.state.auto_pilot.get_current_tick_interval(),
                "last_action": ap_dec.action if ap_dec else "IDLE",
                "last_mode": ap_dec.best_mode if ap_dec else None,
                "last_score": ap_dec.best_score if ap_dec else 0.0,
                "via_retracement": ap_dec.via_retracement if ap_dec else False,
                "signals_generated": self.state.auto_pilot.signals_generated,
            },
            "retracement": {
                "in_retracement": rm.in_retracement if rm else False,
                "zone": rm.zone.value if rm else "NOT_RETRACING",
                "retrace_pct": round(rm.retrace_pct * 100, 1) if rm else 0.0,
                "quality": rm.quality if rm else 0.0,
                "bounce": rm.bounce_detected if rm else False,
                "nearest_fib": rm.nearest_fib if rm else "",
            },
            "decision": {
                "action": de_ctx.action.value if de_ctx else "SCAN_AND_ENTER",
                "lot_scale": de_ctx.lot_scale if de_ctx else 1.0,
                "effective_min_score": de_ctx.effective_min_score if de_ctx else 0.25,
                "paused": de_ctx.adaptive_paused if de_ctx else False,
                "consecutive_losses": de_ctx.consecutive_losses if de_ctx else 0,
                "continuation_prob": (
                    de_ctx.regime.continuation_prob if de_ctx else 0.0
                ),
                "volatility_regime": (
                    de_ctx.regime.volatility_regime if de_ctx else "NORMAL"
                ),
            },
        })

    async def _autopilot_generate_signal(
        self, df, wave_analysis, atr: float, current_price: float,
        decision_ctx=None,
    ) -> None:
        """AutoPilot + DecisionEngine: tự chọn entry mode, tự scale lot, tự quyết định."""
        s = self.state.settings

        # Range boundaries (ORB proxy)
        n_range = max(4, s.monitoring_minutes // 5)
        range_df = df.iloc[-n_range - 1 : -1]
        range_high = float(range_df["high"].max())
        range_low = float(range_df["low"].min())

        # Swing points
        wa_cache = self.state.wave_detector.last_analysis
        swing_high = wa_cache.swing_highs[-1].price if wa_cache and wa_cache.swing_highs else 0.0
        swing_low  = wa_cache.swing_lows[-1].price  if wa_cache and wa_cache.swing_lows  else 0.0

        lot_size = self.state.risk_manager.calculate_lot_size(
            self.state.balance, self.state.equity
        )

        # ── Tự scale: apply DecisionEngine lot multiplier ──────────────── #
        if decision_ctx is not None:
            lot_size = round(lot_size * decision_ctx.lot_scale, 2)
        lot_size = max(s.min_lot, min(s.max_lot, lot_size))

        # ── Extract adaptive params from DecisionContext ───────────────── #
        mwm  = decision_ctx.mode_weight_multipliers if decision_ctx else None
        min_score_override = (
            decision_ctx.effective_min_score if decision_ctx else None
        )

        # ── AutoPilot: scan & score (with adaptive weights) ────────────── #
        best, decision = self.state.auto_pilot.select_best_entry(
            df=df,
            wave_analysis=wave_analysis,
            atr=atr,
            current_price=current_price,
            symbol=s.symbol,
            lot_size=lot_size,
            swing_high=swing_high,
            swing_low=swing_low,
            range_high=range_high,
            range_low=range_low,
            mode_weight_multipliers=mwm,
            override_min_score=min_score_override,
        )

        if best is None:
            logger.debug("AutoPilot: không có setup hợp lệ trên tick này.")
            return

        entry_signal = best.entry_signal

        # ── Wave direction filter ──────────────────────────────────────── #
        # BUY_ONLY: only allow BUY signals; SELL_ONLY: only allow SELL signals
        wave_filter = s.wave_direction_filter.upper()
        if wave_filter == "BUY_ONLY" and entry_signal.direction != "BUY":
            logger.debug(
                "Wave filter BUY_ONLY: skipping %s signal", entry_signal.direction
            )
            return
        if wave_filter == "SELL_ONLY" and entry_signal.direction != "SELL":
            logger.debug(
                "Wave filter SELL_ONLY: skipping %s signal", entry_signal.direction
            )
            return

        # ── Tự mô phỏng: Monte Carlo EV check trước khi submit ─────────── #
        sim = self.state.decision_engine.simulate_candidate(
            entry_price=entry_signal.entry_price,
            sl=entry_signal.sl,
            tp=entry_signal.tp,
            atr=atr,
            direction=entry_signal.direction,
        )
        if sim.expected_value < 0:
            logger.info(
                "AutoPilot [SIM REJECT] EV=%.5f win_prob=%.1f%% — skip",
                sim.expected_value, sim.win_probability * 100,
            )
            return

        priority = self.state.auto_pilot.score_to_priority(best.score)

        logger.info(
            "AutoPilot → mode=%-20s dir=%s score=%.3f rr=%.2f priority=%d "
            "lot=%.2f scale=%.2f EV=%.5f",
            best.entry_mode, best.direction, best.score,
            entry_signal.risk_reward, priority,
            lot_size,
            decision_ctx.lot_scale if decision_ctx else 1.0,
            sim.expected_value,
        )

        coord_signal = CoordSignal(
            signal_id=entry_signal.signal_id,
            symbol=entry_signal.symbol,
            direction=entry_signal.direction,
            entry_price=entry_signal.entry_price,
            sl=entry_signal.sl,
            tp=entry_signal.tp,
            lot_size=entry_signal.lot_size,
            entry_mode=entry_signal.entry_mode,
            priority=priority,
        )
        result = self.state.coordinator.submit_signal(coord_signal)
        logger.debug("AutoPilot signal %s: %s", entry_signal.signal_id, result)

        if "QUEUED" in result:
            await self.state.coordinator.process_next(
                lambda d: self.state.wave_detector.can_trade(d, wave_analysis)
            )
            self._daily_trades += 1


_engine = RobotEngine(app_state)


# ── REST Endpoints ─────────────────────────────────────────────────────── #

@app.get("/api/status", response_model=RobotStatusSchema)
async def get_status():
    wa = app_state.wave_detector.last_analysis
    cm = app_state.coordinator.metrics
    tm = app_state.trade_manager
    uptime = time.time() - app_state.start_time if app_state.robot_running else 0.0
    return RobotStatusSchema(
        running=app_state.robot_running,
        state="RUNNING" if app_state.robot_running else "STOPPED",
        wave_state=wa.main_wave.value if wa else "SIDEWAYS",
        sub_wave=wa.sub_wave.value if wa and wa.sub_wave else None,
        confidence=wa.confidence if wa else 0.0,
        coordinator_state=cm.state.value,
        balance=app_state.balance,
        equity=app_state.equity,
        total_pnl=tm.total_pnl(),
        win_rate=tm.win_rate(),
        profit_factor=tm.profit_factor(),
        total_trades=len(tm.get_closed_trades()),
        open_trades=len(tm.get_open_trades()),
        daily_pnl=app_state.risk_manager.daily_pnl,
        uptime_seconds=uptime,
    )


@app.get("/api/settings", response_model=RobotSettings)
async def get_settings():
    return app_state.settings


@app.post("/api/settings", response_model=RobotSettings)
async def update_settings(settings: RobotSettings, db: Session = Depends(get_db)):
    app_state.settings = settings
    save_settings(db, settings.model_dump())
    app_state.rebuild_components()
    if app_state.robot_running:
        app_state.coordinator.start()
    return app_state.settings


@app.get("/api/trades", response_model=PaginatedTrades)
async def get_trades(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    rows = get_all_trades(db, page, page_size)
    total = get_trade_count(db)
    # Also include in-memory closed trades not yet persisted
    trades = [TradeRecordSchema(**r) for r in rows]
    return PaginatedTrades(trades=trades, total=total, page=page, page_size=page_size)


@app.get("/api/trades/open", response_model=List[TradeRecordSchema])
async def get_open_trades():
    open_trades = app_state.trade_manager.get_open_trades()
    return [TradeRecordSchema(**{
        "trade_id": t.trade_id,
        "symbol": t.symbol,
        "direction": t.direction,
        "lot_size": t.lot_size,
        "entry_price": t.entry_price,
        "sl": t.sl,
        "tp": t.tp,
        "entry_mode": t.entry_mode,
        "open_time": t.open_time,
        "close_time": None,
        "close_price": None,
        "pnl": t.calculate_pnl(
            app_state.data_provider.get_candles(limit=1)["close"].iloc[-1]
            if app_state.data_provider else t.entry_price
        ),
        "status": t.status.value,
        "remaining_lots": t.remaining_lots,
        "be_moved": t.be_moved,
        "grid_level": t.grid_level,
        "comment": t.comment,
    }) for t in open_trades]


@app.post("/api/robot/start")
async def start_robot():
    if app_state.robot_running:
        return {"status": "already_running"}
    app_state.robot_running = True
    app_state.start_time = time.time()
    app_state.coordinator.start()
    app_state._engine_task = asyncio.create_task(_engine.run())
    logger.info("Robot started")
    await app_state.broadcast({"event": "robot_started", "timestamp": time.time()})
    return {"status": "started"}


@app.post("/api/robot/stop")
async def stop_robot():
    if not app_state.robot_running:
        return {"status": "not_running"}
    app_state.robot_running = False
    app_state.coordinator.stop()
    if app_state._engine_task:
        app_state._engine_task.cancel()
        app_state._engine_task = None
    logger.info("Robot stopped")
    await app_state.broadcast({"event": "robot_stopped", "timestamp": time.time()})
    return {"status": "stopped"}


@app.get("/api/wave/analysis", response_model=WaveAnalysisSchema)
async def get_wave_analysis():
    df = app_state.data_provider.get_candles(limit=200)
    wa = app_state.wave_detector.analyse(df)
    can_buy = app_state.wave_detector.can_trade("BUY", wa)
    can_sell = app_state.wave_detector.can_trade("SELL", wa)
    return WaveAnalysisSchema(
        main_wave=wa.main_wave.value,
        sub_wave=wa.sub_wave.value if wa.sub_wave else None,
        confidence=wa.confidence,
        htf_ema_fast=wa.htf_ema_fast,
        htf_ema_slow=wa.htf_ema_slow,
        ltf_ema_fast=wa.ltf_ema_fast,
        ltf_ema_slow=wa.ltf_ema_slow,
        atr=wa.atr,
        swing_highs=[{"index": p.index, "price": p.price, "is_high": p.is_high} for p in wa.swing_highs],
        swing_lows=[{"index": p.index, "price": p.price, "is_high": p.is_high} for p in wa.swing_lows],
        sideways_detected=wa.sideways_detected,
        description=wa.description,
        can_trade_buy=can_buy,
        can_trade_sell=can_sell,
    )


@app.get("/api/queue/status", response_model=QueueStatusSchema)
async def get_queue_status():
    m = app_state.coordinator.metrics
    history = app_state.coordinator.history[:10]
    recent = [
        {
            "signal_id": r.signal.signal_id,
            "symbol": r.signal.symbol,
            "direction": r.signal.direction,
            "status": r.status,
            "reason": r.reject_reason,
            "timestamp": r.signal.timestamp,
        }
        for r in history
    ]
    return QueueStatusSchema(
        signals_queued=m.signals_queued,
        signals_executed=m.signals_executed,
        signals_rejected=m.signals_rejected,
        signals_expired=m.signals_expired,
        queue_depth=m.queue_depth,
        cooldown_until=m.cooldown_until,
        state=m.state.value,
        authority=m.authority.value,
        recent_signals=recent,
    )


@app.get("/api/risk/metrics", response_model=RiskMetricsSchema)
async def get_risk_metrics():
    spread = app_state.data_provider.get_spread_points()
    return RiskMetricsSchema(
        balance=app_state.balance,
        equity=app_state.equity,
        daily_pnl=app_state.risk_manager.daily_pnl,
        peak_equity=app_state.risk_manager.peak_equity,
        martingale_step=app_state.risk_manager.martingale_step,
        consecutive_losses=app_state.risk_manager.consecutive_losses,
        dd_triggered=app_state.risk_manager.dd_triggered,
        daily_profit_locked=app_state.risk_manager.profit_locked,
        daily_loss_locked=app_state.risk_manager.loss_locked,
        lock_reason=app_state.risk_manager.lock_reason,
        open_trades=len(app_state.trade_manager.get_open_trades()),
        spread=spread,
    )


@app.get("/api/risk/daily_lock", response_model=DailyLockStatusSchema)
async def get_daily_lock_status():
    """Trạng thái daily lock — profit/loss lock và lý do dừng."""
    rm = app_state.risk_manager
    s  = app_state.settings
    locked = rm.daily_locked
    return DailyLockStatusSchema(
        profit_locked=rm.profit_locked,
        loss_locked=rm.loss_locked,
        locked=locked,
        lock_reason=rm.lock_reason if locked else "",
        daily_pnl=rm.daily_pnl,
        daily_profit_target=s.daily_profit_target,
        daily_loss_limit=s.daily_loss_limit,
        unlocked_by_user=not locked,
    )


@app.post("/api/robot/reset_daily_lock")
async def reset_daily_lock():
    """
    User manually resets daily profit/loss locks.
    After reset, robot can be restarted normally.
    """
    app_state.risk_manager.reset_daily_locks()
    logger.info("Daily locks reset by user")
    await app_state.broadcast({
        "event": "daily_lock_reset",
        "timestamp": time.time(),
    })
    return {"status": "ok", "message": "Daily profit/loss locks have been reset."}


@app.get("/api/capital/profile", response_model=CapitalProfileSchema)
async def get_capital_profile():
    """Current capital profile and recommended parameters."""
    s       = app_state.settings
    profile_name = s.capital_profile
    if profile_name.upper() == "AUTO":
        profile = app_state.capital_manager.detect(app_state.balance)
    else:
        profile = app_state.capital_manager.get_profile(profile_name)
    return CapitalProfileSchema(
        profile=profile.profile,
        balance=app_state.balance,
        lot_mode=profile.lot_mode,
        lot_value=profile.lot_value,
        max_lot=profile.max_lot,
        max_daily_dd=profile.max_daily_dd_pct,
        max_overall_dd=profile.max_overall_dd_pct,
        risk_per_trade=profile.lot_value,
        max_trades_at_time=profile.max_trades_at_time,
        description=profile.description,
    )


@app.get("/api/capital/suggest_targets")
async def suggest_daily_targets():
    """Suggest daily profit target and loss limit based on current capital and performance."""
    tm = app_state.trade_manager
    win_rate = tm.win_rate()
    closed   = tm.get_closed_trades()
    avg_pnl  = sum(t.pnl for t in closed) / max(len(closed), 1)
    targets  = app_state.capital_manager.suggest_daily_targets(
        balance=app_state.balance,
        profile_name=app_state.settings.capital_profile,
        recent_win_rate=win_rate,
        avg_trade_pnl=avg_pnl,
    )
    return targets


@app.get("/api/candle_library/status", response_model=CandleLibraryStatusSchema)
async def get_candle_library_status():
    """Status of the realtime candle library."""
    s = app_state.candle_library.status()
    return CandleLibraryStatusSchema(**s)


@app.get("/api/llm/status", response_model=LLMStatusSchema)
async def get_llm_status():
    """Status of the LLM Orchestrator."""
    s = app_state.llm.status()
    return LLMStatusSchema(**s)


@app.post("/api/llm/ask")
async def llm_ask(body: dict):
    """
    Ask the LLM a question about the current market/robot state.
    Body: {"prompt": "..."}
    """
    prompt = body.get("prompt", "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")
    answer = app_state.llm.think(prompt)
    return {"answer": answer, "backend": app_state.llm.backend}


# ── AutoPilot status ───────────────────────────────────────────────────── #

def _format_ap_decision(d) -> AutoPilotLastDecisionSchema:
    top = [
        AutoPilotCandidateSchema(
            mode=c["mode"], direction=c["dir"], score=c["score"]
        )
        for c in d.meta.get("all_candidates", [])
    ]
    return AutoPilotLastDecisionSchema(
        timestamp=d.timestamp,
        candidates_evaluated=d.candidates_evaluated,
        candidates_passed=d.candidates_passed,
        best_mode=d.best_mode,
        best_direction=d.best_direction,
        best_score=d.best_score,
        action=d.action,
        signal_id=d.signal_id,
        tick_interval=d.tick_interval,
        via_retracement=d.via_retracement,
        top_candidates=top,
    )


@app.get("/api/autopilot/status", response_model=AutoPilotStatusSchema)
async def get_autopilot_status():
    """Trạng thái chi tiết của AutoPilot — quyết định, điểm, entry modes đã scan."""
    ap = app_state.auto_pilot
    last = _format_ap_decision(ap.last_decision) if ap.last_decision else None
    recent = [_format_ap_decision(d) for d in ap.history[:10]]
    return AutoPilotStatusSchema(
        enabled=True,
        current_tick_interval=ap.get_current_tick_interval(),
        decisions_total=ap.decisions_total,
        signals_generated=ap.signals_generated,
        min_score_threshold=ap.min_score,
        last_decision=last,
        recent_decisions=recent,
    )


@app.get("/api/retracement/status", response_model=RetracementStatusSchema)
async def get_retracement_status():
    """
    Trạng thái real-time của Retracement Engine.
    Operator giám sát: sóng hồi đang ở zone nào, quality bao nhiêu,
    điểm vào/SL/TP an toàn nhất là gì.
    """
    rm = app_state.retracement_engine.last_measure
    if rm is None:
        return RetracementStatusSchema(
            in_retracement=False,
            main_direction="BUY",
            zone="NOT_RETRACING",
            retrace_pct=0.0,
            nearest_fib="0.500",
            quality=0.0,
            bounce_detected=False,
            impulse_start=0.0,
            impulse_end=0.0,
            current_price=0.0,
            safest_entry=0.0,
            safest_sl=0.0,
            safest_tp=0.0,
            tp_extension=0.0,
            risk_reward=0.0,
        )

    sr_schemas = [
        SupportResistanceLevelSchema(
            price=s.price,
            strength=s.strength,
            sr_type=s.sr_type,
            touch_count=s.touch_count,
        )
        for s in rm.sr_levels
    ]
    return RetracementStatusSchema(
        in_retracement=rm.in_retracement,
        main_direction=rm.main_direction,
        zone=rm.zone.value,
        retrace_pct=round(rm.retrace_pct, 4),
        nearest_fib=rm.nearest_fib,
        quality=rm.quality,
        bounce_detected=rm.bounce_detected,
        impulse_start=rm.impulse_start,
        impulse_end=rm.impulse_end,
        current_price=rm.current_price,
        safest_entry=rm.safest_entry,
        safest_sl=rm.safest_sl,
        safest_tp=rm.safest_tp,
        tp_extension=rm.tp_extension,
        risk_reward=rm.risk_reward,
        fib_levels=rm.fib_levels,
        sr_levels=sr_schemas,
        description=rm.description,
    )


# ── Decision Engine endpoints ──────────────────────────────────────────── #

@app.get("/api/decision/status", response_model=DecisionEngineStatusSchema)
async def get_decision_status():
    """
    Trạng thái đầy đủ của Decision Engine — não bộ vận hành.

    Operator giám sát:
      - action hiện tại (SCAN | HOLD | REDUCE | FORCE_PAUSE | SCALE_UP)
      - lot_scale đang áp dụng
      - circuit breaker state
      - performance thống kê toàn cục + per segment
      - adaptive weight adjustments đã học được
      - 10 kết quả trade gần nhất được học
    """
    de  = app_state.decision_engine
    ctx = de.last_context
    gs  = de.tracker.get_global_stats()

    regime = None
    if ctx:
        regime = MarketRegimeSchema(
            continuation_prob=ctx.regime.continuation_prob,
            volatility_regime=ctx.regime.volatility_regime,
            momentum_score=ctx.regime.momentum_score,
            atr_percentile=ctx.regime.atr_percentile,
        )

    segment_stats = {
        key: SegmentStatsSchema(
            win_rate=st.win_rate,
            profit_factor=st.profit_factor,
            avg_rr=st.avg_rr,
            expectancy=st.expectancy,
            sample_size=st.sample_size,
        )
        for key, st in de.tracker.get_all_segment_stats().items()
    }

    recent = [
        {
            "mode":         o.mode,
            "wave_state":   o.wave_state,
            "direction":    o.direction,
            "retrace_zone": o.retrace_zone,
            "pnl":          round(o.pnl, 2),
            "rr_achieved":  round(o.rr_achieved, 3),
        }
        for o in de.tracker.get_recent_outcomes(10)
    ]

    adaptive = de.adaptive_summary

    return DecisionEngineStatusSchema(
        last_action=ctx.action.value if ctx else "SCAN_AND_ENTER",
        lot_scale=de.controller.get_lot_scale(),
        effective_min_score=de.controller.get_effective_min_score(),
        adaptive_paused=adaptive["is_paused"],
        pause_reason=adaptive["pause_reason"],
        consecutive_losses=adaptive["consecutive_losses"],
        adaptation_count=adaptive["adaptation_count"],
        regime=regime,
        global_stats=SegmentStatsSchema(
            win_rate=gs.win_rate,
            profit_factor=gs.profit_factor,
            avg_rr=gs.avg_rr,
            expectancy=gs.expectancy,
            sample_size=gs.sample_size,
        ),
        segment_stats=segment_stats,
        mode_weight_adjs=adaptive["mode_weight_adjs"],
        recent_outcomes=recent,
    )


@app.post("/api/decision/reset-pause")
async def reset_decision_pause():
    """
    Tự sửa lỗi: reset circuit breaker thủ công.
    Khi AdaptiveController tự PAUSE sau nhiều lần thua liên tiếp,
    operator có thể reset sau khi đã kiểm tra tình trạng thị trường.
    """
    app_state.decision_engine.reset_adaptive_pause()
    return {
        "status": "ok",
        "lot_scale": app_state.decision_engine.controller.get_lot_scale(),
        "is_paused": app_state.decision_engine.controller.is_paused,
    }


@app.get("/api/performance/dashboard", response_model=PerformanceDashboardSchema)
async def get_performance_dashboard():
    """
    Bộ não trung tâm — bảng điều khiển tổng hợp của PerformanceTracker.

    Trả về:
      - Thống kê tổng hợp toàn hệ thống (global win_rate, profit_factor, …)
      - Số lượng pattern WIN và LOSS đã học được
      - Top 5 pattern WIN (ưu tiên đặt lệnh)
      - Top 5 pattern LOSS (tránh hoặc block)
      - Thông tin consultation gần nhất (kết quả pipeline gate cuối)
    """
    tracker = app_state.decision_engine.tracker
    dash    = tracker.summary_dashboard()

    def _pattern_schema(p: dict, is_win: bool) -> PatternSummarySchema:
        fp = p["fingerprint"]
        return PatternSummarySchema(
            fingerprint=TradeFingerprintSchema(
                mode=fp["mode"],
                wave_state=fp["wave_state"],
                direction=fp["direction"],
                retrace_zone=fp["retrace_zone"],
                session=fp["session"],
                volatility=fp["volatility"],
                hour=fp["hour"],
                dow=fp["dow"],
            ),
            win_rate=p.get("win_rate"),
            loss_rate=p.get("loss_rate"),
            total=p["total"],
            avg_pnl=p["avg_pnl"],
        )

    return PerformanceDashboardSchema(
        total_recorded=dash["total_recorded"],
        pattern_count=dash["pattern_count"],
        global_win_rate=dash["global_win_rate"],
        global_profit_factor=dash["global_profit_factor"],
        global_avg_rr=dash["global_avg_rr"],
        global_expectancy=dash["global_expectancy"],
        global_sample_size=dash["global_sample_size"],
        consecutive_losses=dash["consecutive_losses"],
        win_patterns_count=dash["win_patterns_count"],
        loss_patterns_count=dash["loss_patterns_count"],
        top_win_patterns=[_pattern_schema(p, True)  for p in dash["top_win_patterns"]],
        top_loss_patterns=[_pattern_schema(p, False) for p in dash["top_loss_patterns"]],
        last_consultation=dash.get("last_consultation"),
    )


@app.get("/api/performance/consult", response_model=PreTradeConsultationSchema)
async def consult_trade(
    mode:        str = Query(..., description="Entry mode: BREAKOUT | RETRACE | …"),
    wave_state:  str = Query(..., description="BULL_MAIN | BEAR_MAIN | SIDEWAYS"),
    direction:   str = Query(..., description="BUY | SELL"),
    retrace_zone: str = Query("NOT_RETRACING", description="RetracementZone value"),
    atr:         float = Query(0.0, description="Current ATR value"),
    price:       float = Query(0.0, description="Current price"),
):
    """
    Pipeline gate thủ công — kiểm tra trước khi đặt lệnh.

    Gọi endpoint này để hỏi bộ não trung tâm:
      - Có nên trade pattern này không? (should_trade)
      - Xác suất WIN là bao nhiêu?
      - Pattern này có bị BLOCK không? Lý do?
      - Priority boost nếu là WIN pattern?

    Đây là phiên bản API của PIPELINE MANDATORY consult().
    """
    consultation = app_state.decision_engine.consult_before_entry(
        mode=mode,
        wave_state=wave_state,
        direction=direction,
        retrace_zone=retrace_zone,
        atr=atr,
        price=price,
    )
    return PreTradeConsultationSchema(
        should_trade=consultation.should_trade,
        win_probability=consultation.win_probability,
        loss_risk=consultation.loss_risk,
        authority=consultation.authority,
        block_reason=consultation.block_reason,
        pattern_known=consultation.pattern_known,
        pattern_win_rate=consultation.pattern_win_rate,
        global_win_rate=consultation.global_win_rate,
        priority_boost=consultation.priority_boost,
        consultation_id=consultation.consultation_id,
        timestamp=consultation.timestamp,
    )


@app.get("/api/candles", response_model=List[CandleSchema])
async def get_candles(
    symbol: str = Query("EURUSD"),
    tf: str = Query("M5"),
    limit: int = Query(100, ge=10, le=500),
):
    df = app_state.data_provider.get_candles(limit=limit, timeframe=tf)
    return [
        CandleSchema(
            timestamp=r["timestamp"],
            open=r["open"],
            high=r["high"],
            low=r["low"],
            close=r["close"],
            volume=r["volume"],
            datetime=str(r["datetime"]),
        )
        for r in df.to_dict("records")
    ]


# ── WebSocket ──────────────────────────────────────────────────────────── #

@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket):
    await websocket.accept()
    app_state._ws_clients.add(websocket)
    try:
        # Send initial state
        status = await get_status()
        await websocket.send_json({"event": "init", "status": status.model_dump()})
        # Keep connection alive
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                if data == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                await websocket.send_json({"event": "heartbeat", "ts": time.time()})
    except WebSocketDisconnect:
        pass
    finally:
        app_state._ws_clients.discard(websocket)


# ── Broker status ──────────────────────────────────────────────────────── #

@app.get("/api/broker/status", response_model=BrokerStatusSchema)
async def get_broker_status():
    """Trả về trạng thái kết nối tới cTrader (hoặc Mock nếu chưa cấu hình)."""
    dp = app_state.data_provider
    if isinstance(dp, CTraderDataProvider):
        st = dp.status
        return BrokerStatusSchema(
            provider_type=st.provider_type,
            connected=st.connected,
            app_authenticated=st.app_authenticated,
            account_authenticated=st.account_authenticated,
            history_loaded=st.history_loaded,
            symbol=st.symbol,
            symbol_id=st.symbol_id,
            timeframe=st.timeframe,
            live=st.live,
            last_error=st.last_error,
            last_tick_ts=st.last_tick_ts,
            bars_loaded=st.bars_loaded,
            account_id=st.account_id,
        )
    # MockDataProvider
    return BrokerStatusSchema(
        provider_type="MOCK",
        connected=True,
        app_authenticated=False,
        account_authenticated=False,
        history_loaded=True,
        symbol=getattr(dp, "symbol", ""),
        symbol_id=0,
        timeframe=getattr(dp, "timeframe", ""),
        live=False,
        last_error="Chưa cấu hình CTRADER_CLIENT_ID/SECRET/ACCESS_TOKEN — đang dùng dữ liệu giả lập.",
        last_tick_ts=0.0,
        bars_loaded=len(dp.get_candles(limit=500)),
        account_id=0,
    )


# ── Health check ───────────────────────────────────────────────────────── #

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": time.time()}


# ── Warm-up API ────────────────────────────────────────────────────────── #

@app.get("/api/warmup/status")
async def warmup_status():
    """
    Trả về kết quả lần warm-up gần nhất.
    lstm_ready / ensemble_ready / win_classifier_ready = True nghĩa là model
    đã sẵn sàng hoạt động (đã qua ngưỡng cold-start).
    """
    report = app_state.warmup_report
    if report is None:
        return {"status": "not_run", "message": "Warm-up chưa được chạy"}
    return {"status": "ok", **report.to_dict()}


@app.post("/api/warmup/run")
async def warmup_run(
    lstm_samples: int = 25,
    outcome_samples: int = 10,
    label_noise: float = 0.05,
):
    """
    Chạy lại warm-up pipeline thủ công.

    Hữu ích khi:
    - Thay đổi symbol/timeframe và cần reset model
    - Model bị confused sau quá nhiều lệnh thua liên tiếp
    - Muốn boost lại learning speed

    Parameters
    ----------
    lstm_samples    : số lượng candle sequence mỗi wave state (default 25)
    outcome_samples : số lượng trade outcome mỗi (mode, wave_state) (default 10)
    label_noise     : xác suất flip label để tránh overfitting (default 0.05)
    """
    try:
        pipeline = WarmUpPipeline(
            decision_engine=app_state.decision_engine,
            wave_detector=app_state.wave_detector,
            lstm_samples=lstm_samples,
            outcome_samples=outcome_samples,
            label_noise=label_noise,
        )
        report = pipeline.run()
        app_state.warmup_report  = report
        app_state.warmup_pipeline = pipeline
        return {"status": "ok", **report.to_dict()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ── Evolution API ──────────────────────────────────────────────────────── #

@app.get("/api/evolution/status")
async def evolution_status():
    """
    Trả về kết quả lần evolution gần nhất.

    Bao gồm:
    - best_genome: chiến lược tốt nhất tìm được
    - best_fitness: profit_factor, win_rate, max_drawdown của chiến lược đó
    - generation_bests: fitness tốt nhất mỗi generation
    - applied_to_live: True nếu đã apply vào live system
    """
    result = app_state.evolution_result
    if result is None:
        return {"status": "not_run", "message": "Evolution chưa được chạy"}
    return {"status": "ok", **result.to_dict()}


@app.post("/api/evolution/run")
async def evolution_run(
    pop_size:         int   = 20,
    generations:      int   = 5,
    episodes:         int   = 10,
    bars_per_episode: int   = 80,
    apply_to_live:    bool  = False,
):
    """
    Chạy evolutionary self-play: tạo môi trường giả lập, để các chiến lược
    cạnh tranh, và chọn ra chiến lược tiến hóa tốt nhất.

    Parameters
    ----------
    pop_size         : số lượng agent trong population (default 20)
    generations      : số vòng tiến hóa (default 5)
    episodes         : số lượng market episode mỗi agent (default 10)
    bars_per_episode : số candle bar mỗi episode (default 80)
    apply_to_live    : nếu True, tự động apply best genome vào live system

    Kết quả
    -------
    Trả về EvolutionResult bao gồm best_genome và toàn bộ population stats.
    Nếu apply_to_live=True, DecisionEngine sẽ cập nhật:
      - mode_weight_adjs  (ưu tiên mode tốt nhất)
      - base_min_score    (ngưỡng entry)
      - lot_scale         (kích thước lệnh)
    """
    try:
        engine = EvolutionaryEngine(
            pop_size         = pop_size,
            generations      = generations,
            episodes         = episodes,
            bars_per_episode = bars_per_episode,
        )
        result = engine.run()
        app_state.evolution_engine = engine
        app_state.evolution_result = result

        if apply_to_live:
            result.apply_to(app_state.decision_engine)

        return {"status": "ok", **result.to_dict()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/evolution/apply")
async def evolution_apply():
    """
    Apply kết quả evolution gần nhất vào live DecisionEngine.

    Cập nhật:
    - mode_weight_adjs của AdaptiveController
    - base_min_score (ngưỡng entry quality)
    - lot_scale (kích thước lệnh)

    Chỉ có tác dụng nếu đã chạy /api/evolution/run trước đó.
    """
    result = app_state.evolution_result
    if result is None:
        raise HTTPException(
            status_code=400,
            detail="Chưa có kết quả evolution. Hãy chạy POST /api/evolution/run trước."
        )
    if result.applied_to_live:
        return {
            "status": "already_applied",
            "message": "Best genome đã được apply trước đó",
            "applied_genome": result.best_genome.to_dict(),
        }
    result.apply_to(app_state.decision_engine)
    return {
        "status": "ok",
        "message": "Best genome đã được apply vào live system",
        "applied_genome": result.best_genome.to_dict(),
        "best_fitness": result.best_fitness.to_dict(),
    }


# ── Meta-Learning API ──────────────────────────────────────────────────── #

@app.get("/api/meta/status")
async def meta_status():
    """
    Trả về kết quả meta-learning gần nhất.

    Bao gồm:
    - best_genome     : chiến lược tốt nhất qua tất cả outer loop
    - gene_importances: tầm quan trọng của từng gene (importance, mean_winner_value, keep_confidence)
    - gene_insights   : giải thích bằng ngôn ngữ tự nhiên vì sao winner thắng
    - outer_loop_bests: fitness tốt nhất mỗi outer loop
    """
    result = app_state.meta_result
    if result is None:
        return {"status": "not_run", "message": "Meta-learning chưa được chạy"}
    return {"status": "ok", **result.to_dict()}


@app.get("/api/meta/gene_insights")
async def meta_gene_insights():
    """
    Trả về phân tích ngôn ngữ tự nhiên về gene chiến lược:
    - Gene nào DOMINANT (tương quan cao với win)
    - Gene nào CONSERVED (hội tụ nhất quán ở winners)
    - Gene nào NEUTRAL (có thể tự do đột biến)

    Chỉ có dữ liệu sau khi đã chạy POST /api/meta/run.
    """
    result = app_state.meta_result
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Chưa có kết quả meta-learning. Hãy chạy POST /api/meta/run trước."
        )
    return {
        "status":         "ok",
        "gene_insights":  result.gene_insights,
        "gene_importances": {
            k: v.to_dict() for k, v in result.gene_importances.items()
        },
    }


@app.post("/api/meta/run")
async def meta_run(
    outer_loops:      int  = 3,
    pop_size:         int  = 20,
    generations:      int  = 5,
    episodes:         int  = 10,
    bars_per_episode: int  = 80,
    top_k_winners:    int  = 5,
    apply_to_live:    bool = False,
):
    """
    Chạy Meta-Learning + Strategy Genome Engine.

    Đây là cấp độ cao nhất: không chỉ tiến hóa chiến lược mà còn
    học ra VÌ SAO winner thắng và dùng kiến thức đó để breed chiến lược
    thông minh hơn cho vòng tiến hóa tiếp theo.

    Quá trình (mỗi outer_loop):
      1. Evolve: chạy EvolutionaryEngine × generations
      2. Analyse: WinnerAnalyzer học gene importance từ top-K winners
      3. Accumulate: GenePool lưu gene values × fitness của winners
      4. Breed: StrategyGenetics dùng GenePool để breed next generation
         (importance-weighted crossover + guided mutation)

    Parameters
    ----------
    outer_loops      : số vòng lặp Evolve→Analyse→Breed (default 3)
    pop_size         : agents per evolution run (default 20)
    generations      : generations per evolution run (default 5)
    episodes         : market episodes per agent (default 10)
    bars_per_episode : candle bars per episode (default 80)
    top_k_winners    : số winner dùng để học gene importance (default 5)
    apply_to_live    : nếu True, tự động apply best genome vào live system

    Kết quả
    -------
    - best_genome với chiến lược tốt nhất qua toàn bộ meta-learning
    - gene_importances cho từng gene
    - gene_insights giải thích vì sao winner thắng
    """
    try:
        engine = MetaLearningEngine(
            outer_loops      = outer_loops,
            pop_size         = pop_size,
            generations      = generations,
            episodes         = episodes,
            bars_per_episode = bars_per_episode,
            top_k_winners    = top_k_winners,
        )
        result = engine.run()
        app_state.meta_engine = engine
        app_state.meta_result = result

        if apply_to_live:
            result.apply_to(app_state.decision_engine)

        return {"status": "ok", **result.to_dict()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/meta/apply")
async def meta_apply():
    """
    Apply kết quả meta-learning gần nhất vào live DecisionEngine.

    Cập nhật:
    - mode_weight_adjs của AdaptiveController (dựa trên gene tốt nhất)
    - base_min_score
    - lot_scale

    Chỉ có tác dụng nếu đã chạy /api/meta/run trước đó.
    """
    result = app_state.meta_result
    if result is None:
        raise HTTPException(
            status_code=400,
            detail="Chưa có kết quả meta-learning. Hãy chạy POST /api/meta/run trước."
        )
    if result.applied_to_live:
        return {
            "status":         "already_applied",
            "message":        "Meta-learned genome đã được apply trước đó",
            "applied_genome": result.best_genome.to_dict(),
        }
    result.apply_to(app_state.decision_engine)
    return {
        "status":         "ok",
        "message":        "Meta-learned genome đã được apply vào live system",
        "applied_genome": result.best_genome.to_dict(),
        "best_fitness":   result.best_fitness.to_dict(),
        "gene_insights":  result.gene_insights,
    }


# ── Causal Strategy Intelligence API ──────────────────────────────────── #

@app.get("/api/causal/status")
async def causal_status():
    """
    Trả về kết quả causal analysis gần nhất.

    Bao gồm:
    - causal_scorecards     : mỗi gene có causal_score, spurious_score, regime_robustness
    - counterfactual_genome : chiến lược tối ưu từ world model (không phải từ evolution)
    - world_model_r2        : chất lượng của world model per regime
    - causal_insights       : giải thích nhân quả bằng ngôn ngữ tự nhiên
    """
    result = app_state.causal_result
    if result is None:
        return {"status": "not_run", "message": "Causal analysis chưa được chạy"}
    return {"status": "ok", **result.to_dict()}


@app.get("/api/causal/insights")
async def causal_insights():
    """
    Trả về phân tích nhân quả bằng ngôn ngữ tự nhiên:

    - Gene nào THẬT SỰ GÂY RA thắng lợi (causal, not just correlated)
    - Gene nào chỉ là tương quan giả (spurious)
    - Gene nào sống sót qua tất cả market regime
    - World model đề xuất giá trị tối ưu cho từng gene là bao nhiêu

    Chỉ có dữ liệu sau khi đã chạy POST /api/causal/run.
    """
    result = app_state.causal_result
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Chưa có kết quả causal. Hãy chạy POST /api/causal/run trước."
        )
    return {
        "status":          "ok",
        "causal_insights": result.causal_insights,
        "causal_scorecards": {
            k: v.to_dict() for k, v in result.causal_scorecards.items()
        },
        "world_model_r2": result.world_model_r2,
    }


@app.post("/api/causal/run")
async def causal_run(
    n_samples:        int  = 30,
    episodes:         int  = 8,
    bars_per_episode: int  = 70,
    intervention_m:   int  = 8,
    apply_to_live:    bool = False,
):
    """
    Chạy World Model + Causal Strategy Engine.

    Đây là tầng cao nhất của intelligence stack — vượt qua correlation
    để học thật sự nhân quả chiến lược:

    1. Data Collection: sample N genomes × 3 regimes (BULL/BEAR/SIDEWAYS)
       → ma trận (genome_vector, regime, fitness)

    2. World Model: fit linear P(fitness | genome, regime) bằng OLS
       → biết được trong thế giới tổng hợp, gene nào ảnh hưởng thế nào

    3. Causal Analysis (3 phương pháp song song):
       a) Intervention effect: perturb gene ±Δ, đo ΔFitness (ablation study)
       b) Cross-regime consistency: |r| per regime → spurious nếu chỉ đúng 1 regime
       c) Partial correlation: kiểm soát tất cả gene khác → r thuần tuý

    4. Counterfactual Genome: world model argmax — suy ra chiến lược tối ưu
       ngay cả khi không có dữ liệu trực tiếp

    Parameters
    ----------
    n_samples        : số genome sample cho data collection (default 30)
    episodes         : market episodes per evaluation (default 8)
    bars_per_episode : candle bars per episode (default 70)
    intervention_m   : số base genome dùng cho ablation study (default 8)
    apply_to_live    : nếu True, tự động apply counterfactual genome vào live

    Kết quả
    -------
    - causal_scorecards: Dict[gene → {causal_score, spurious_score, regime_robustness, ...}]
    - counterfactual_genome: chiến lược suy diễn từ world model
    - causal_insights: giải thích nhân quả bằng ngôn ngữ tự nhiên
    """
    try:
        engine = CausalStrategyEngine(
            n_samples        = n_samples,
            episodes         = episodes,
            bars_per_episode = bars_per_episode,
            intervention_m   = intervention_m,
        )
        result = engine.run()
        app_state.causal_engine = engine
        app_state.causal_result = result

        if apply_to_live:
            result.apply_to(app_state.decision_engine)

        return {"status": "ok", **result.to_dict()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/causal/apply")
async def causal_apply():
    """
    Apply kết quả causal analysis vào live DecisionEngine.

    Áp dụng counterfactual genome — chiến lược được suy diễn từ world model
    (causally optimal strategy), không phải từ simple evolution.

    Chỉ có tác dụng nếu đã chạy /api/causal/run trước đó.
    """
    result = app_state.causal_result
    if result is None:
        raise HTTPException(
            status_code=400,
            detail="Chưa có kết quả causal. Hãy chạy POST /api/causal/run trước."
        )
    if result.applied_to_live:
        return {
            "status":         "already_applied",
            "message":        "Counterfactual genome đã được apply trước đó",
            "applied_genome": result.counterfactual_genome.to_dict(),
        }
    result.apply_to(app_state.decision_engine)
    return {
        "status":              "ok",
        "message":             "Causal counterfactual genome đã được apply vào live system",
        "applied_genome":      result.counterfactual_genome.to_dict(),
        "causal_insights":     result.causal_insights,
        "world_model_r2":      result.world_model_r2,
    }


# ── Utility Optimization API ───────────────────────────────────────────── #

@app.post("/api/utility/run")
async def utility_run(
    n_genomes:           int   = 25,
    episodes:            int   = 8,
    bars_per_episode:    int   = 70,
    growth_weight:       float = 0.35,
    trust_weight:        float = 0.30,
    stability_weight:    float = 0.20,
    speed_weight:        float = 0.10,
    dominance_weight:    float = 0.05,
    risk_aversion:       float = 0.40,
    time_preference:     float = 0.60,
    kelly_safety_factor: float = 0.25,
    apply_to_live:       bool  = False,
):
    """
    Chạy Decision Theory + Utility Optimization Engine.

    Đây là tầng cao nhất của intelligence stack — rational strategic agent.
    Hệ không chỉ tối ưu win/loss hay causal_score, mà tối ưu theo utility
    đa chiều dài hạn:

    1. Growth utility  : E[log(wealth)] — Kelly-optimal geometric growth
    2. Trust utility   : (1 − max_drawdown) × profit_factor — không blow up
    3. Stability utility: Sharpe-like equity smoothness
    4. Speed utility   : trade frequency — more opportunities vs noise
    5. Dominance utility: long-term vs short-term performance split

    Trade-offs được kiểm soát bởi UtilityConfig:
    - growth_weight vs trust_weight    → growth vs capital preservation
    - speed_weight vs stability_weight → frequency vs smoothness
    - time_preference                  → myopic vs far-sighted dominance
    - risk_aversion                    → arithmetic vs log-return optimisation
    - kelly_safety_factor              → fraction of Kelly for lot sizing

    Params
    ------
    n_genomes           : số genomes cần evaluate (default 25)
    episodes            : số episodes mỗi genome (default 8)
    bars_per_episode    : số bars mỗi episode (default 70)
    growth_weight       : trọng số utility growth (default 0.35)
    trust_weight        : trọng số utility trust/drawdown (default 0.30)
    stability_weight    : trọng số utility equity smoothness (default 0.20)
    speed_weight        : trọng số utility trade frequency (default 0.10)
    dominance_weight    : trọng số utility long-term dominance (default 0.05)
    risk_aversion       : 0=risk-neutral, 1=fully risk-averse (default 0.40)
    time_preference     : 0=myopic, 1=far-sighted (default 0.60)
    kelly_safety_factor : fraction of Kelly criterion (default 0.25)
    apply_to_live       : tự động apply optimal genome sau khi chạy
    """
    cfg = UtilityConfig(
        growth_weight       = growth_weight,
        trust_weight        = trust_weight,
        stability_weight    = stability_weight,
        speed_weight        = speed_weight,
        dominance_weight    = dominance_weight,
        risk_aversion       = risk_aversion,
        time_preference     = time_preference,
        kelly_safety_factor = kelly_safety_factor,
    )
    engine = UtilityOptimizationEngine(
        n_genomes        = n_genomes,
        episodes         = episodes,
        bars_per_episode = bars_per_episode,
        utility_config   = cfg,
    )
    app_state.utility_engine = engine
    result = engine.run()
    app_state.utility_result = result

    if apply_to_live:
        result.apply_to(app_state.decision_engine)

    return {
        "status":         "ok",
        "n_genomes":      result.n_genomes,
        "pareto_count":   len(result.pareto_indices),
        "duration_secs":  result.duration_secs,
        "kelly_lot_scale": result.kelly_lot_scale,
        "optimal_utility": result.optimal_utility.to_dict(),
        "optimal_genome":  result.optimal_genome.to_dict(),
        "utility_insights": result.utility_insights,
        "applied_to_live":  result.applied_to_live,
    }


@app.get("/api/utility/status")
async def utility_status():
    """
    Trả về kết quả utility optimization gần nhất.

    Bao gồm:
    - optimal_genome     : genome tối ưu theo expected utility
    - optimal_utility    : breakdown 5 chiều utility
    - kelly_lot_scale    : lot size tối ưu theo Kelly criterion
    - pareto_indices     : chỉ số các genome Pareto-efficient
    - all_utilities      : composite utility của tất cả genome
    - utility_config     : cấu hình UtilityConfig đã dùng
    - utility_insights   : phân tích trade-off bằng ngôn ngữ tự nhiên
    """
    result = app_state.utility_result
    if result is None:
        return {"status": "not_run", "message": "Utility optimization chưa được chạy"}
    return {"status": "ok", **result.to_dict()}


@app.get("/api/utility/pareto")
async def utility_pareto():
    """
    Trả về dữ liệu Pareto frontier để visualisation.

    Mỗi genome bao gồm:
    - genome_idx  : chỉ số trong population
    - is_pareto   : True nếu genome này là Pareto-efficient
    - utility     : UtilityVector (5 chiều + composite)
    - genome      : thông số genome chính (min_score, tp_rr, lot_scale...)

    Pareto-efficient strategy = không có strategy nào tốt hơn trên TẤT CẢ 5 chiều.
    Đây là tập hợp "chiến lược không bị dominated" — mỗi điểm đại diện cho
    một trade-off khác nhau giữa growth/trust/stability/speed/dominance.
    """
    result = app_state.utility_result
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Chưa có kết quả utility. Hãy chạy POST /api/utility/run trước."
        )
    return {
        "status":          "ok",
        "pareto_data":     result.pareto_data(),
        "pareto_count":    len(result.pareto_indices),
        "n_genomes":       result.n_genomes,
        "optimal_idx":     int(
            max(result.pareto_indices or range(result.n_genomes),
                key=lambda i: result.utility_vectors[i].composite)
        ),
    }


@app.post("/api/utility/configure")
async def utility_configure(
    growth_weight:       float = 0.35,
    trust_weight:        float = 0.30,
    stability_weight:    float = 0.20,
    speed_weight:        float = 0.10,
    dominance_weight:    float = 0.05,
    risk_aversion:       float = 0.40,
    time_preference:     float = 0.60,
    kelly_safety_factor: float = 0.25,
):
    """
    Cập nhật UtilityConfig mà KHÔNG cần chạy lại simulation.

    Sử dụng khi bạn muốn thay đổi trade-off preferences mà không tốn thời gian
    re-run toàn bộ evaluation. Engine sẽ recompute utility vectors và chọn lại
    optimal genome với config mới.

    Ví dụ:
    - risk_aversion=0.8 → bảo thủ hơn, ưu tiên capital preservation
    - growth_weight=0.5 → aggressive growth, chấp nhận rủi ro cao hơn
    - time_preference=0.9 → ưu tiên strategies tốt trong RECENT trades
    - kelly_safety_factor=0.1 → dùng chỉ 10% Kelly fraction
    """
    result = app_state.utility_result
    if result is None:
        raise HTTPException(
            status_code=400,
            detail="Chưa có kết quả utility. Hãy chạy POST /api/utility/run trước."
        )
    new_cfg = UtilityConfig(
        growth_weight       = growth_weight,
        trust_weight        = trust_weight,
        stability_weight    = stability_weight,
        speed_weight        = speed_weight,
        dominance_weight    = dominance_weight,
        risk_aversion       = risk_aversion,
        time_preference     = time_preference,
        kelly_safety_factor = kelly_safety_factor,
    )
    app_state.utility_engine.reconfigure(new_cfg)
    # reconfigure() updates last_result in place
    result = app_state.utility_result
    return {
        "status":            "reconfigured",
        "new_config":        new_cfg.to_dict(),
        "new_optimal_utility": result.optimal_utility.to_dict() if result else None,
        "new_kelly_lot_scale": result.kelly_lot_scale if result else None,
        "utility_insights":    result.utility_insights if result else [],
    }


@app.post("/api/utility/apply")
async def utility_apply():
    """
    Apply kết quả utility optimization vào live DecisionEngine.

    Áp dụng:
    - optimal_genome   : mode_weights, min_score (từ utility-maximising strategy)
    - kelly_lot_scale  : lot size tối ưu theo Kelly criterion (risk-aversion adjusted)

    Đây là bước cuối cùng của intelligence stack:
    Evolution → Meta-Learning → Causal Intelligence → Rational Agent (Utility)

    Rational agent chọn chiến lược theo expected utility đa chiều, không chỉ
    maximise một metric. Kelly criterion đảm bảo lot size tối ưu về geometric
    growth rate.
    """
    result = app_state.utility_result
    if result is None:
        raise HTTPException(
            status_code=400,
            detail="Chưa có kết quả utility. Hãy chạy POST /api/utility/run trước."
        )
    if result.applied_to_live:
        return {
            "status":            "already_applied",
            "message":           "Rational agent policy đã được apply trước đó",
            "optimal_genome":    result.optimal_genome.to_dict(),
            "kelly_lot_scale":   result.kelly_lot_scale,
            "optimal_utility":   result.optimal_utility.to_dict(),
        }
    result.apply_to(app_state.decision_engine)
    return {
        "status":            "ok",
        "message":           "Rational agent policy (utility-optimal + Kelly lot) đã được apply",
        "optimal_genome":    result.optimal_genome.to_dict(),
        "kelly_lot_scale":   result.kelly_lot_scale,
        "optimal_utility":   result.optimal_utility.to_dict(),
        "utility_insights":  result.utility_insights,
    }


# ── Game Theory + Ecosystem API ────────────────────────────────────────── #

@app.post("/api/ecosystem/run")
async def ecosystem_run(
    n_opponents:         int   = 5,
    n_candidate_genomes: int   = 15,
    episodes:            int   = 6,
    bars_per_episode:    int   = 70,
    nash_iterations:     int   = 8,
    exploitation_rate:   float = 0.5,
    impact_coefficient:  float = 0.15,
    impact_decay:        float = 0.80,
    apply_to_live:       bool  = False,
):
    """
    Chạy Multi-Agent Game Theory + Market Ecosystem Engine.

    Đây là tầng cao nhất của intelligence stack — strategic ecosystem intelligence.
    Hệ không chỉ tối ưu utility của bản thân trong chân không, mà tối ưu trong
    môi trường có đối thủ, thuật toán nền tảng, và market impact:

    Opponent Types (5 loại):
    - MOMENTUM_FOLLOWER : chase trends → crowds our entries, fades extremes
    - MEAN_REVERTER     : fade extremes → counter-trades breakouts
    - NOISE_TRADER      : random entries → random slippage injection
    - MARKET_MAKER      : provide liquidity → adverse-selection risk
    - TREND_FADER       : exploit late-trend crowding → challenges our momentum plays

    Phases:
    1. Spawn opponent agents với diverse behavioral profiles
    2. Evaluate N candidate genomes trong multi-agent ecosystem với market impact
    3. Iterative Best Response (IBR) → Nash equilibrium approximation
    4. Exploitability scoring per opponent type (0=unexploitable, 1=fully exploitable)
    5. Build ecosystem insights + strategic recommendations

    Params
    ------
    n_opponents         : số lượng opponents (default 5)
    n_candidate_genomes : số genomes để evaluate (default 15)
    episodes            : số market episodes (default 6)
    bars_per_episode    : số bars mỗi episode (default 70)
    nash_iterations     : max IBR iterations for Nash (default 8)
    exploitation_rate   : how aggressively opponents adapt (0=static, 1=adaptive)
    impact_coefficient  : price impact per unit net crowd (default 0.15)
    impact_decay        : market impact decay per bar (default 0.80)
    apply_to_live       : tự động apply best response genome sau khi chạy
    """
    cfg = EcosystemConfig(
        n_opponents         = n_opponents,
        n_candidate_genomes = n_candidate_genomes,
        episodes            = episodes,
        bars_per_episode    = bars_per_episode,
        nash_iterations     = nash_iterations,
        exploitation_rate   = exploitation_rate,
        impact_coefficient  = impact_coefficient,
        impact_decay        = impact_decay,
    )
    engine = GameTheoryEngine(config=cfg)
    app_state.ecosystem_engine = engine
    result = engine.run()
    app_state.ecosystem_result = result

    if apply_to_live:
        result.apply_to(app_state.decision_engine)

    return {
        "status":                "ok",
        "nash_value":            result.nash_equilibrium.nash_value,
        "ecosystem_pf":          result.ecosystem_pf,
        "isolation_pf":          result.isolation_pf,
        "pf_delta":              round(result.ecosystem_pf - result.isolation_pf, 3),
        "n_opponents":           result.n_opponents,
        "exploitability":        result.exploitability,
        "duration_secs":         result.duration_secs,
        "best_response_genome":  result.best_response_genome.to_dict(),
        "ecosystem_insights":    result.ecosystem_insights,
        "applied_to_live":       result.applied_to_live,
    }


@app.get("/api/ecosystem/status")
async def ecosystem_status():
    """
    Trả về kết quả game theory / ecosystem simulation gần nhất.

    Bao gồm:
    - best_response_genome : genome tối ưu trong multi-agent ecosystem
    - nash_equilibrium     : Nash equilibrium details + opponent profile
    - exploitability       : Dict[opponent_type → score ∈ [0,1]]
    - ecosystem_pf         : profit_factor trong ecosystem (có market impact)
    - isolation_pf         : profit_factor benchmark (không có opponents)
    - impact_stats         : crowding %, avg slippage, max impact
    - ecosystem_insights   : phân tích chiến lược bằng ngôn ngữ tự nhiên
    """
    result = app_state.ecosystem_result
    if result is None:
        return {"status": "not_run", "message": "Ecosystem simulation chưa được chạy"}
    return {"status": "ok", **result.to_dict()}


@app.get("/api/ecosystem/nash")
async def ecosystem_nash():
    """
    Trả về chi tiết Nash equilibrium từ lần chạy gần nhất.

    Nash equilibrium = trạng thái mà không agent nào có thể cải thiện
    kết quả của mình bằng cách đơn phương thay đổi chiến lược.

    Bao gồm:
    - our_strategy      : chiến lược tốt nhất của chúng ta tại equilibrium
    - opponent_profile  : aggression của mỗi loại opponent tại equilibrium
    - is_approximate    : luôn True (exact Nash requires LP for continuous games)
    - iterations_used   : số IBR iterations để converge
    - convergence_gap   : |strategy[t] - strategy[t-1]| tại vòng cuối
    - nash_value        : profit_factor của chúng ta tại equilibrium
    """
    result = app_state.ecosystem_result
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Chưa có kết quả ecosystem. Hãy chạy POST /api/ecosystem/run trước."
        )
    return {
        "status":          "ok",
        "nash_equilibrium": result.nash_equilibrium.to_dict(),
        "exploitability":  result.exploitability,
        "impact_stats":    result.impact_stats.to_dict(),
        "ecosystem_config": result.ecosystem_config.to_dict(),
    }


@app.post("/api/ecosystem/apply")
async def ecosystem_apply():
    """
    Apply best-response genome từ Nash equilibrium vào live DecisionEngine.

    Áp dụng chiến lược tối ưu trong multi-agent ecosystem:
    - mode_weights  : đã tối ưu để chống lại các opponent types
    - min_score     : điều chỉnh theo exploitability và market impact
    - lot_scale     : được cân nhắc theo mức độ crowding

    Đây là bước cuối của intelligence stack:
    Evolution → Meta-Learning → Causal Intelligence → Rational Agent
    → Strategic Ecosystem Intelligence (Game Theory + Nash Equilibrium)

    Chiến lược được chọn không chỉ tối ưu utility đa chiều mà còn
    là best response trong môi trường có đối thủ thực tế.
    """
    result = app_state.ecosystem_result
    if result is None:
        raise HTTPException(
            status_code=400,
            detail="Chưa có kết quả ecosystem. Hãy chạy POST /api/ecosystem/run trước."
        )
    if result.applied_to_live:
        return {
            "status":               "already_applied",
            "message":              "Ecosystem best-response policy đã được apply trước đó",
            "best_response_genome": result.best_response_genome.to_dict(),
            "nash_value":           result.nash_equilibrium.nash_value,
        }
    result.apply_to(app_state.decision_engine)
    return {
        "status":               "ok",
        "message":              "Ecosystem best-response (Nash equilibrium) đã được apply",
        "best_response_genome": result.best_response_genome.to_dict(),
        "nash_value":           result.nash_equilibrium.nash_value,
        "exploitability":       result.exploitability,
        "ecosystem_insights":   result.ecosystem_insights,
    }


# ── Sovereign Oversight API ────────────────────────────────────────────── #

@app.post("/api/sovereign/run")
async def sovereign_run(
    mode:                      str   = "ADVISORY",
    objective_level:           str   = "GROWTH",
    max_lot_override:          float = 2.0,
    min_lot_override:          float = 0.0,
    kill_threshold:            float = 0.15,
    throttle_threshold:        float = 0.35,
    boost_threshold:           float = 0.70,
    attention_normalize:       bool  = True,
    max_attention_per_cluster: float = 0.50,
):
    """
    Chạy một chu kỳ Strategic Sovereign Oversight.

    Đây là tầng tối cao (layer 7) của intelligence stack — governs toàn bộ
    ecosystem như một hệ điều hành chiến lược:

    Phases
    ------
    1. Thu thập telemetry từ toàn bộ engine clusters (evolution/meta/causal/utility/ecosystem).
    2. Auto-detect objective level từ risk state của hệ thống.
    3. Phân bổ attention budget theo objective hierarchy và cluster scores.
    4. Ban hành governance directives: SCALE_UP | THROTTLE | SUSPEND | KILL | MAINTAIN.
    5. Build governance insights + objective tree snapshot.
    6. Ghi audit trail.

    Sovereign Modes
    ---------------
    ADVISORY  : tính toán directives nhưng KHÔNG apply vào live system.
                Dùng để tham khảo và xem xét trước khi enforce.
    SEMI_AUTO : áp dụng MAINTAIN/THROTTLE tự động; KILL/SCALE_UP cần /apply.
    FULL_AUTO : áp dụng TẤT CẢ directives ngay sau khi run().

    Objective Levels (tự động phát hiện từ risk state, có thể override)
    ---------------
    SURVIVAL   : hệ thống ở ngưỡng nguy hiểm — đóng tất cả aggressive clusters.
    STABILITY  : dampening — giảm lot, throttle volatile clusters.
    GROWTH     : bình thường — thưởng ROI cao, phạt value thấp.
    DOMINANCE  : tấn công — boost winners mạnh, kill laggards nhanh.

    Guardrails
    ----------
    - Không bao giờ override risk hard-limits của RiskManager.
    - lot_scale luôn nằm trong [min_lot_override, max_lot_override].
    - Kill-switch toàn hệ nếu survival triggered.

    Parameters
    ----------
    mode                      : ADVISORY | SEMI_AUTO | FULL_AUTO (default ADVISORY)
    objective_level           : SURVIVAL | STABILITY | GROWTH | DOMINANCE (default GROWTH)
    max_lot_override          : hard cap on lot_scale (default 2.0)
    min_lot_override          : floor on lot_scale, 0=no floor (default 0.0)
    kill_threshold            : sv ≤ này → KILL (default 0.15)
    throttle_threshold        : sv ≤ này → THROTTLE (default 0.35)
    boost_threshold           : sv ≥ này → SCALE_UP (default 0.70)
    attention_normalize       : chuẩn hoá attention sum=1.0 (default True)
    max_attention_per_cluster : cap attention share per cluster (default 0.50)
    """
    try:
        sovereign_mode = SovereignMode(mode.upper())
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"mode không hợp lệ: '{mode}'. Chọn một trong: ADVISORY, SEMI_AUTO, FULL_AUTO."
        )
    try:
        obj_level = ObjectiveLevel(objective_level.upper())
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"objective_level không hợp lệ: '{objective_level}'. "
                   "Chọn một trong: SURVIVAL, STABILITY, GROWTH, DOMINANCE."
        )

    policy = SovereignPolicy(
        mode                     = sovereign_mode,
        objective_level          = obj_level,
        max_lot_override         = max_lot_override,
        min_lot_override         = min_lot_override,
        kill_threshold           = kill_threshold,
        throttle_threshold       = throttle_threshold,
        boost_threshold          = boost_threshold,
        attention_normalize      = attention_normalize,
        max_attention_per_cluster= max_attention_per_cluster,
    )

    try:
        engine = SovereignOversightEngine(policy=policy)
        result = engine.run(app_state)
        app_state.sovereign_engine = engine
        app_state.sovereign_result = result

        return {
            "status":               "ok",
            "cycle_id":             result.cycle_id,
            "objective_level":      result.objective_tree.active_level.value,
            "survival_triggered":   result.objective_tree.survival_triggered,
            "healthy_clusters":     result.objective_tree.healthy_clusters,
            "total_clusters":       result.objective_tree.total_clusters,
            "directives_summary":   {
                cid: d.directive.value for cid, d in result.directives.items()
            },
            "resource_allocation":  {k: round(v, 4) for k, v in result.resource_allocation.items()},
            "governance_insights":  result.governance_insights,
            "duration_secs":        result.duration_secs,
            "applied_to_live":      result.applied_to_live,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/sovereign/status")
async def sovereign_status():
    """
    Trả về kết quả sovereign oversight cycle gần nhất.

    Bao gồm:
    - cycle_id            : ID của cycle gần nhất
    - cluster_states      : telemetry + strategic_value của từng cluster
    - directives          : governance directive per cluster với rationale
    - resource_allocation : attention budget per cluster
    - sovereign_policy    : policy đang hiệu lực
    - objective_tree      : NetworkObjectiveTree snapshot
    - governance_insights : phân tích chiến lược bằng ngôn ngữ tự nhiên
    - audit_trail         : lịch sử governance (tối đa 500 entries)
    """
    result = app_state.sovereign_result
    if result is None:
        return {
            "status": "not_run",
            "message": "Sovereign oversight chưa được chạy. Hãy gọi POST /api/sovereign/run.",
        }
    return {"status": "ok", **result.to_dict()}


@app.get("/api/sovereign/policy")
async def sovereign_policy_endpoint():
    """
    Trả về sovereign policy hiện tại và objective tree snapshot.

    Sử dụng để kiểm tra:
    - mode đang hoạt động (ADVISORY/SEMI_AUTO/FULL_AUTO)
    - objective level hiện tại
    - kill/throttle/boost thresholds
    - network-level objective tree (survival/stability/growth/dominance scores)

    Không cần chạy /api/sovereign/run trước.
    """
    result = app_state.sovereign_result
    policy = app_state.sovereign_engine.policy

    policy_info: Dict[str, Any] = {
        "status":           "ok",
        "sovereign_policy": policy.to_dict(),
        "objective_hierarchy": {
            "levels": ["SURVIVAL", "STABILITY", "GROWTH", "DOMINANCE"],
            "description": {
                "SURVIVAL":   "Emergency: drawdown critical — kill all aggressive clusters",
                "STABILITY":  "Conservative: dampen — reduce lots, throttle volatile clusters",
                "GROWTH":     "Normal: reward high-ROI, penalise low strategic value",
                "DOMINANCE":  "Offensive: scale winners hard, kill laggards fast",
            },
            "active_level": policy.objective_level.value,
        },
        "directive_types": {
            "SCALE_UP":  "Boost lot_scale +20%, increase attention budget",
            "THROTTLE":  "Halve attention, cap lot_scale at 70% current",
            "SUSPEND":   "Zero attention — cluster pending first run",
            "KILL":      "Zero attention + hard lot_scale cap 0.25",
            "MAINTAIN":  "No change — nominal performance",
        },
    }

    if result is not None:
        policy_info["last_cycle_id"]     = result.cycle_id
        policy_info["last_completed_at"] = result.completed_at
        policy_info["objective_tree"]    = result.objective_tree.to_dict()
        policy_info["cluster_summary"]   = {
            cid: {
                "lifecycle":        cs.lifecycle.value,
                "strategic_value":  round(cs.strategic_value, 4),
                "attention_budget": round(cs.attention_budget, 4),
            }
            for cid, cs in result.cluster_states.items()
        }

    return policy_info


@app.post("/api/sovereign/apply")
async def sovereign_apply():
    """
    Áp dụng governance directives từ sovereign oversight cycle gần nhất vào live system.

    Những gì được áp dụng
    ---------------------
    - SCALE_UP  : lot_scale × 1.20 (capped by max_lot_override)
    - THROTTLE  : lot_scale × 0.70
    - KILL      : lot_scale capped to 0.25 (minimal viable trading)
    - MAINTAIN  : no change

    Guardrails luôn được enforce:
    - lot_scale ∈ [min_lot_override, max_lot_override]
    - RiskManager drawdown flags không bao giờ bị override

    Chỉ áp dụng được sau khi đã chạy POST /api/sovereign/run.
    """
    result = app_state.sovereign_result
    if result is None:
        raise HTTPException(
            status_code=400,
            detail="Chưa có kết quả sovereign. Hãy chạy POST /api/sovereign/run trước."
        )
    if result.applied_to_live:
        return {
            "status":          "already_applied",
            "message":         "Sovereign directives đã được apply trước đó",
            "cycle_id":        result.cycle_id,
            "directives":      {cid: d.directive.value for cid, d in result.directives.items()},
            "objective_level": result.objective_tree.active_level.value,
        }

    result.apply_to(app_state)

    # Mark audit entries for this cycle as applied
    for entry in result.audit_trail:
        if entry.cycle_id == result.cycle_id:
            entry.applied = True

    return {
        "status":              "ok",
        "message":             "Sovereign governance directives đã được apply vào live system",
        "cycle_id":            result.cycle_id,
        "objective_level":     result.objective_tree.active_level.value,
        "directives_applied":  {cid: d.directive.value for cid, d in result.directives.items()},
        "governance_insights": result.governance_insights,
    }


@app.get("/api/sovereign/dominance")
async def sovereign_dominance():
    """
    Trả về Network Dominance Score — portfolio-level dominance metrics.

    Mục tiêu cốt lõi: **max total network dominance, not local wins**.

    Chỉ số này đo lường toàn bộ ecosystem như một quỹ đầu tư attention:

    Metrics
    -------
    raw_dominance          : Σ(sv_i × α_i) — weighted strategic value toàn portfolio
    risk_adjusted_dominance: raw × (1 − portfolio_risk) — sau khi trừ rủi ro
    portfolio_risk         : Σ(risk_i × α_i) — rủi ro tổng hợp của portfolio
    portfolio_efficiency   : raw / max_possible — 1.0 = toàn bộ attention vào cluster tốt nhất
    concentration_hhi      : Herfindahl index ∈ [1/N, 1]
                             • 0.20 = rải đều 5 clusters (đa dạng hoá tối đa)
                             • 1.00 = tập trung 100% vào 1 cluster
    n_active_clusters      : số cluster đang đóng góp vào dominance
    delta_vs_previous      : thay đổi raw_dominance so với cycle trước
    trajectory             : IMPROVING | STABLE | DECLINING

    Portfolio Optimization Logic
    ----------------------------
    Attention được phân bổ theo bài toán LP:
      maximize: Σ_i (sv_i − λ × risk_i) × α_i
      subject to: Σ_i α_i = 1,  0 ≤ α_i ≤ max_attention_per_cluster

    Với λ phụ thuộc vào objective level:
      SURVIVAL  → λ=5.0 (chỉ giữ cluster ít rủi ro nhất)
      STABILITY → λ=1.5 (giảm mạnh cluster rủi ro cao)
      GROWTH    → λ=0.5 (cân bằng)
      DOMINANCE → λ=0.2 (ưu tiên gần như toàn bộ vào sv)

    Yêu cầu POST /api/sovereign/run trước.
    """
    result = app_state.sovereign_result
    if result is None:
        return {
            "status": "not_run",
            "message": "Chưa có kết quả sovereign. Hãy gọi POST /api/sovereign/run.",
        }

    nd = result.network_dominance
    allocs = result.resource_allocation
    cluster_contributions = {
        cid: {
            "strategic_value":   round(result.cluster_states[cid].strategic_value, 4),
            "attention":         round(allocs.get(cid, 0.0), 4),
            "dominance_contrib": round(
                result.cluster_states[cid].strategic_value * allocs.get(cid, 0.0), 4
            ),
            "directive":         result.directives[cid].directive.value,
            "lifecycle":         result.cluster_states[cid].lifecycle.value,
        }
        for cid in result.cluster_states
    }

    # Sort clusters by dominance contribution descending
    ranked = sorted(
        cluster_contributions.items(),
        key=lambda x: x[1]["dominance_contrib"],
        reverse=True,
    )

    return {
        "status":                   "ok",
        "cycle_id":                 result.cycle_id,
        "network_dominance":        nd.to_dict(),
        "objective_level":          result.objective_tree.active_level.value,
        "cluster_contributions":    dict(ranked),
        "portfolio_summary": {
            "total_clusters":    result.objective_tree.total_clusters,
            "active_clusters":   nd.n_active_clusters,
            "portfolio_risk":    round(nd.portfolio_risk, 4),
            "efficiency_pct":    round(nd.portfolio_efficiency * 100, 2),
            "concentration_hhi": round(nd.concentration_hhi, 4),
            "is_concentrated":   nd.concentration_hhi > 0.70,
        },
    }


# ── Autonomous Enterprise API ──────────────────────────────────────────── #

async def _enterprise_background_loop() -> None:
    """
    Background asyncio task for the Autonomous Enterprise Engine.

    Runs enterprise cycles indefinitely until stop() is requested,
    sleeping for the adaptive cycle interval between cycles.
    """
    engine = app_state.enterprise_engine
    while not engine.is_stop_requested():
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, engine.run, app_state
            )
        except Exception as exc:
            logger.error("Enterprise background loop error: %s", exc)
        # Adaptive interval — may change each cycle based on objective
        interval = engine.current_cycle_interval()
        logger.info(
            "Enterprise loop: sleeping %.0f seconds (objective=%s)",
            interval,
            engine.last_cycle.objective_level if engine.last_cycle else "GROWTH",
        )
        await asyncio.sleep(interval)
    engine.lifecycle = EnterpriseLifecycle.STOPPED
    logger.info("Enterprise background loop: stopped.")


@app.post("/api/enterprise/start")
async def enterprise_start(
    cycle_interval_secs:      float = 900.0,
    min_cycle_interval_secs:  float = 60.0,
    max_cycle_interval_secs:  float = 3600.0,
    evolution_cycle_interval: int   = 3,
    meta_cycle_interval:      int   = 2,
    causal_cycle_interval:    int   = 2,
    utility_cycle_interval:   int   = 1,
    ecosystem_cycle_interval: int   = 2,
    auto_evolve:              bool  = True,
):
    """
    Khởi động SELF-EVOLVING AUTONOMOUS ENTERPRISE (Layer 8).

    Đây là bước cuối cùng: hệ thống trở thành một thực thể vận hành độc lập.

    Hệ thống sẽ:
    - **Tự sinh chiến lược**: orchestrates tất cả 7 layers trong mỗi cycle
    - **Tự phân bổ tài nguyên**: Sovereign Oversight (FULL_AUTO) tự động phân bổ
    - **Tự vận hành**: chạy liên tục trong background asyncio task
    - **Tự tối ưu**: monitor dominance và điều chỉnh từng cycle
    - **Tự tiến hóa qua thời gian**: PolicyEvolver tự mutate governance policy

    Layer Staleness (cycle_interval giữa các lần chạy mỗi layer)
    --------------------------------------------------------------
    - evolution  : mỗi N enterprise cycles (default 3 — expensive)
    - meta       : mỗi N cycles (default 2)
    - causal     : mỗi N cycles (default 2)
    - utility    : mỗi N cycles (default 1 — chạy mọi cycle)
    - ecosystem  : mỗi N cycles (default 2)

    Adaptive Cycle Interval
    -----------------------
    - SURVIVAL mode   → min_cycle_interval_secs (kiểm tra thường xuyên)
    - DOMINANCE mode  → max_cycle_interval_secs (không cần vội)
    - Các mode khác   → cycle_interval_secs (mặc định)

    Parameters
    ----------
    cycle_interval_secs     : giây giữa các enterprise cycles (default 900 = 15 phút)
    min_cycle_interval_secs : tối thiểu (dùng khi SURVIVAL) (default 60)
    max_cycle_interval_secs : tối đa (dùng khi DOMINANCE) (default 3600)
    evolution_cycle_interval: số enterprise cycles giữa các lần chạy evolution (default 3)
    meta_cycle_interval     : ... meta (default 2)
    causal_cycle_interval   : ... causal (default 2)
    utility_cycle_interval  : ... utility (default 1)
    ecosystem_cycle_interval: ... ecosystem (default 2)
    auto_evolve             : bật/tắt self-evolution của policy (default True)
    """
    engine = app_state.enterprise_engine

    if engine.lifecycle == EnterpriseLifecycle.RUNNING:
        return {
            "status":  "already_running",
            "message": "Autonomous Enterprise đã đang chạy",
            "cycle_n": engine.cycle_count,
        }

    # Reconfigure with requested params
    cfg = EnterpriseConfig(
        cycle_interval_secs      = cycle_interval_secs,
        min_cycle_interval_secs  = min_cycle_interval_secs,
        max_cycle_interval_secs  = max_cycle_interval_secs,
        evolution_cycle_interval = evolution_cycle_interval,
        meta_cycle_interval      = meta_cycle_interval,
        causal_cycle_interval    = causal_cycle_interval,
        utility_cycle_interval   = utility_cycle_interval,
        ecosystem_cycle_interval = ecosystem_cycle_interval,
        auto_evolve              = auto_evolve,
    )
    app_state.enterprise_engine = AutonomousEnterpriseEngine(config=cfg)
    engine = app_state.enterprise_engine
    engine.start()

    # Launch background loop
    task = asyncio.create_task(
        _enterprise_background_loop()
    )
    app_state.enterprise_task = task

    return {
        "status":  "started",
        "message": (
            "🚀 SELF-EVOLVING AUTONOMOUS ENTERPRISE đã khởi động. "
            "Thực thể sẽ tự vận hành, tự tối ưu, và tự tiến hóa."
        ),
        "config":  cfg.to_dict(),
    }


@app.post("/api/enterprise/stop")
async def enterprise_stop():
    """
    Dừng Autonomous Enterprise Engine.

    Gửi tín hiệu dừng — enterprise sẽ hoàn thành cycle hiện tại
    rồi mới dừng hẳn (không kill giữa chừng).

    Toàn bộ lịch sử cycles và champion policy được giữ lại trong memory.
    Có thể restart bằng POST /api/enterprise/start.
    """
    engine = app_state.enterprise_engine

    if engine.lifecycle != EnterpriseLifecycle.RUNNING:
        return {
            "status":  "not_running",
            "message": f"Enterprise không đang chạy (lifecycle={engine.lifecycle.value})",
        }

    engine.stop()

    if app_state.enterprise_task is not None:
        app_state.enterprise_task.cancel()
        app_state.enterprise_task = None

    return {
        "status":  "stopped",
        "message": "Autonomous Enterprise đã dừng. Memory và champion policy được giữ lại.",
        "cycle_n": engine.cycle_count,
        "champion": (
            {
                "cycle_n":       engine.memory.champion.cycle_n,
                "raw_dominance": round(engine.memory.champion.raw_dominance, 4),
            }
            if engine.memory.champion else None
        ),
    }


@app.get("/api/enterprise/status")
async def enterprise_status():
    """
    Trả về trạng thái hiện tại của Autonomous Enterprise.

    Bao gồm:
    - lifecycle       : IDLE | RUNNING | STOPPED
    - cycle_n         : số enterprise cycles đã hoàn thành
    - objective_level : sovereign objective level của cycle gần nhất
    - raw_dominance   : network dominance của cycle gần nhất
    - trend           : IMPROVING | STABLE | DECLINING
    - champion        : chu kỳ tốt nhất từ trước đến nay
    - last_cycle      : chi tiết cycle gần nhất (layer records + insights)

    Không cần start trước — trả về IDLE nếu chưa khởi động.
    """
    return {"status": "ok", **app_state.enterprise_engine.status()}


@app.post("/api/enterprise/evolve")
async def enterprise_evolve():
    """
    Buộc chạy một enterprise cycle ngay lập tức (đồng bộ).

    Hữu ích để:
    - Kiểm tra hoạt động trước khi start background loop
    - Force một cycle cụ thể khi đang ở IDLE hoặc STOPPED
    - Debug và xem kết quả đầy đủ của một cycle

    Không ảnh hưởng đến background loop (nếu đang chạy, cycle này
    chạy song song — không nên dùng đồng thời).

    Kết quả trả về
    --------------
    - cycle_id            : ID của cycle này
    - enterprise_cycle_n  : thứ tự cycle
    - layer_records       : kết quả từng layer (ran/skipped/success/summary)
    - sovereign_cycle_id  : cycle ID của sovereign oversight
    - raw_dominance       : network dominance đạt được
    - objective_level     : sovereign objective
    - policy_evolved      : True nếu governance policy được tự tiến hóa
    - policy_mutation     : mô tả chi tiết mutation (nếu có)
    - insights            : phân tích enterprise bằng ngôn ngữ tự nhiên
    - duration_secs       : thời gian hoàn thành cycle
    """
    try:
        cycle = await asyncio.get_event_loop().run_in_executor(
            None, app_state.enterprise_engine.run, app_state
        )
        return {"status": "ok", **cycle.to_dict()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/enterprise/manifest")
async def enterprise_manifest():
    """
    Trả về "consciousness snapshot" của Autonomous Enterprise.

    Đây là cái nhìn toàn diện về thực thể — trạng thái tự nhận thức của nó:

    entity         : tên và layer số
    description    : mô tả bản chất của thực thể
    lifecycle      : IDLE | RUNNING | STOPPED
    cycle_n        : tổng số cycles đã hoàn thành
    current_policy : sovereign policy đang áp dụng (sau các lần self-evolve)
    memory         : lịch sử dominance, trend, champion
    layer_schedule : schedule của từng layer (interval, staleness, due)
    next_cycle_secs: giây đến cycle tiếp theo
    config         : toàn bộ EnterpriseConfig
    recent_cycles  : chi tiết 10 cycles gần nhất

    Cấu trúc intelligence stack
    ---------------------------
    Layer 1  — WaveDetector         : phát hiện sóng thị trường
    Layer 2  — SignalCoordinator    : phối hợp tín hiệu
    Layer 3  — DecisionEngine       : quyết định chiến lược
    Layer 4  — EvolutionaryEngine   : tiến hóa chiến lược (self-play)
    Layer 5  — MetaLearningEngine   : học tại sao winner thắng
    Layer 6  — CausalStrategyEngine : phân tích nhân quả
               UtilityOptimizationEngine : tối ưu đa chiều utility
               GameTheoryEngine    : Nash equilibrium + multi-agent
    Layer 7  — SovereignOversightEngine : quản trị toàn bộ ecosystem
    Layer 8  — AutonomousEnterpriseEngine: SELF-EVOLVING AUTONOMOUS ENTERPRISE ← đây
    """
    manifest = app_state.enterprise_engine.manifest()
    manifest["intelligence_stack"] = {
        "layer_1": "WaveDetector — market wave detection",
        "layer_2": "SignalCoordinator — signal orchestration",
        "layer_3": "DecisionEngine — strategy decisions",
        "layer_4": "EvolutionaryEngine — self-play genetic evolution",
        "layer_5": "MetaLearningEngine — why winners win",
        "layer_6": "CausalStrategyEngine + UtilityOptimizationEngine + GameTheoryEngine",
        "layer_7": "SovereignOversightEngine — network-level governance",
        "layer_8": "AutonomousEnterpriseEngine — SELF-EVOLVING AUTONOMOUS ENTERPRISE",
    }
    return {"status": "ok", **manifest}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
