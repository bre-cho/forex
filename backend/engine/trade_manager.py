"""
Trade Manager — Partial close, trailing stop, break-even, time-based exit,
grid system, trade lifecycle.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class TradeStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    PARTIAL = "PARTIAL"


@dataclass
class TradeRecord:
    trade_id: str
    symbol: str
    direction: str          # BUY / SELL
    lot_size: float
    entry_price: float
    sl: float
    tp: float
    entry_mode: str
    open_time: float = field(default_factory=time.time)
    close_time: Optional[float] = None
    close_price: Optional[float] = None
    pnl: float = 0.0
    status: TradeStatus = TradeStatus.OPEN
    remaining_lots: float = 0.0
    be_moved: bool = False          # SL moved to break-even
    grid_level: int = 0
    comment: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.remaining_lots == 0.0:
            self.remaining_lots = self.lot_size

    @property
    def is_open(self) -> bool:
        return self.status == TradeStatus.OPEN

    def calculate_pnl(self, current_price: float, pip_value: float = 10.0, pip_size: float = 0.0001) -> float:
        # pip_size defaults to 0.0001 (standard FX); caller should pass the real
        # broker instrument spec value to avoid using a hardcoded constant.
        effective_pip_size = pip_size if pip_size > 0 else 0.0001
        if self.direction.upper() == "BUY":
            pips = (current_price - self.entry_price) / effective_pip_size
        else:
            pips = (self.entry_price - current_price) / effective_pip_size
        return round(pips * pip_value * self.remaining_lots, 2)


@dataclass
class PartialCloseConfig:
    enabled: bool = False
    trigger_pct: float = 50.0     # close when TP% reached
    close_pct: float = 50.0       # % of lots to close
    move_sl_to_be: bool = True    # move SL to BE after partial


@dataclass
class TrailingConfig:
    enabled: bool = False
    mode: str = "PCT_TP"          # PCT_TP or HILO
    trigger_pct: float = 50.0     # start trailing after X% TP hit
    trail_pct: float = 30.0       # trail at X% of TP distance behind


@dataclass
class GridConfig:
    enabled: bool = False
    levels: int = 3
    distance_pips: float = 200.0
    distance_multiplier: float = 1.5
    volume_multiplier: float = 1.5
    max_grid_lot: float = 1.0


@dataclass
class BreakEvenConfig:
    """Move SL to break-even (entry price + offset) after profit reaches trigger_pips."""
    enabled: bool = False
    trigger_pips: float = 20.0   # pips in profit before moving SL to BE
    offset_pips: float = 2.0     # SL = entry ± offset_pips (covers spread)
    pip_size: float = 0.0001


@dataclass
class TimeBasedExitConfig:
    """Close a stagnant trade if it has been open longer than max_duration_minutes
    without reaching min_profit_pips.  Set min_profit_pips=0 to close regardless
    of PnL direction."""
    enabled: bool = False
    max_duration_minutes: float = 240.0   # 4 hours default
    min_profit_pips: float = 0.0          # only exit if profit < this threshold


class PartialCloseManager:
    def __init__(self, config: PartialCloseConfig) -> None:
        self.config = config
        self._triggered: Dict[str, bool] = {}

    def check_and_close(self, trade: TradeRecord, current_price: float) -> Optional[float]:
        """
        Returns lots_to_close if partial close should trigger, else None.
        Modifies trade in place (remaining_lots, sl adjustment).
        """
        if not self.config.enabled:
            return None
        if trade.trade_id in self._triggered:
            return None

        tp_dist = abs(trade.tp - trade.entry_price)
        if tp_dist <= 0:
            return None

        price_dist = abs(current_price - trade.entry_price)
        pct_reached = price_dist / tp_dist * 100

        if pct_reached >= self.config.trigger_pct:
            lots_to_close = round(trade.remaining_lots * (self.config.close_pct / 100), 8)
            lots_to_close = min(lots_to_close, trade.remaining_lots)
            trade.remaining_lots = round(trade.remaining_lots - lots_to_close, 8)

            if self.config.move_sl_to_be:
                trade.sl = trade.entry_price
                trade.be_moved = True

            self._triggered[trade.trade_id] = True
            logger.info(
                "Partial close: trade %s — closed %.2f lots at %.5f (%.0f%% of TP)",
                trade.trade_id,
                lots_to_close,
                current_price,
                pct_reached,
            )
            return lots_to_close
        return None


class TrailingStopManager:
    def __init__(self, config: TrailingConfig) -> None:
        self.config = config
        self._best_price: Dict[str, float] = {}
        self._trailing_active: Dict[str, bool] = {}

    def update(self, trade: TradeRecord, current_price: float, atr: float = 0.0) -> Optional[float]:
        """
        Returns updated SL price if trailing stop should move, else None.
        """
        if not self.config.enabled:
            return None

        tid = trade.trade_id
        is_buy = trade.direction.upper() == "BUY"

        # Track best price
        if tid not in self._best_price:
            self._best_price[tid] = current_price
        else:
            if is_buy:
                self._best_price[tid] = max(self._best_price[tid], current_price)
            else:
                self._best_price[tid] = min(self._best_price[tid], current_price)

        tp_dist = abs(trade.tp - trade.entry_price)
        if tp_dist <= 0:
            return None

        # Check if trailing should activate
        price_dist = abs(current_price - trade.entry_price)
        pct_reached = price_dist / tp_dist * 100

        if pct_reached < self.config.trigger_pct:
            return None

        self._trailing_active[tid] = True

        if self.config.mode == "PCT_TP":
            trail_dist = tp_dist * (self.config.trail_pct / 100)
        else:  # HILO — trail by ATR
            trail_dist = atr if atr > 0 else tp_dist * 0.3

        best = self._best_price[tid]
        if is_buy:
            new_sl = best - trail_dist
            if new_sl > trade.sl:
                return round(new_sl, 5)
        else:
            new_sl = best + trail_dist
            if new_sl < trade.sl:
                return round(new_sl, 5)

        return None


class GridManager:
    """Calculates grid entry levels based on multipliers."""

    def __init__(self, config: GridConfig) -> None:
        self.config = config

    def get_grid_levels(
        self, base_price: float, direction: str, pip_size: float = 0.0001
    ) -> List[Dict]:
        """
        Returns list of {'price': float, 'lot': float, 'level': int}
        """
        levels = []
        is_buy = direction.upper() == "BUY"
        dist = self.config.distance_pips * pip_size
        base_lot = 0.01  # will be overridden by lot manager

        for i in range(1, self.config.levels + 1):
            level_dist = dist * (self.config.distance_multiplier ** (i - 1))
            if is_buy:
                price = base_price - level_dist
            else:
                price = base_price + level_dist
            lot = min(
                base_lot * (self.config.volume_multiplier ** (i - 1)),
                self.config.max_grid_lot,
            )
            levels.append({"price": round(price, 5), "lot": round(lot, 8), "level": i})
        return levels


class BreakEvenManager:
    """
    Moves SL to break-even once trade profit reaches trigger_pips.

    Triggered once per trade.  After BE is set the SL can only move
    further in the direction of the trade (never backward) — the
    TrailingStopManager handles that.
    """

    def __init__(self, config: BreakEvenConfig) -> None:
        self.config = config
        self._triggered: Dict[str, bool] = {}

    def check_and_move(
        self, trade: TradeRecord, current_price: float
    ) -> Optional[float]:
        """
        Returns new SL price if break-even should trigger, else None.
        Marks trade.be_moved = True on trigger.
        """
        if not self.config.enabled:
            return None
        if self._triggered.get(trade.trade_id):
            return None
        if trade.be_moved:
            # already moved (e.g. by partial close manager)
            self._triggered[trade.trade_id] = True
            return None

        pip = self.config.pip_size
        trigger_dist = self.config.trigger_pips * pip
        offset_dist = self.config.offset_pips * pip

        is_buy = trade.direction.upper() == "BUY"
        if is_buy:
            profit_dist = current_price - trade.entry_price
            if profit_dist >= trigger_dist:
                new_sl = round(trade.entry_price + offset_dist, 5)
                if new_sl > trade.sl:   # only move SL in profitable direction
                    trade.sl = new_sl
                    trade.be_moved = True
                    self._triggered[trade.trade_id] = True
                    logger.info(
                        "BreakEven: trade %s SL → %.5f (BE+%.1f pips)",
                        trade.trade_id, new_sl, self.config.offset_pips,
                    )
                    return new_sl
        else:
            profit_dist = trade.entry_price - current_price
            if profit_dist >= trigger_dist:
                new_sl = round(trade.entry_price - offset_dist, 5)
                if new_sl < trade.sl:   # only move SL in profitable direction
                    trade.sl = new_sl
                    trade.be_moved = True
                    self._triggered[trade.trade_id] = True
                    logger.info(
                        "BreakEven: trade %s SL → %.5f (BE-%.1f pips)",
                        trade.trade_id, new_sl, self.config.offset_pips,
                    )
                    return new_sl

        return None


class TimeBasedExitManager:
    """
    Closes a stagnant trade that has been open longer than max_duration_minutes
    and has not reached min_profit_pips.

    This prevents capital being tied up in directionless positions.
    """

    def __init__(self, config: TimeBasedExitConfig) -> None:
        self.config = config

    def should_close(
        self, trade: TradeRecord, current_price: float, pip_size: float = 0.0001
    ) -> bool:
        """
        Returns True if the trade should be time-closed.
        """
        if not self.config.enabled:
            return False

        elapsed_minutes = (time.time() - trade.open_time) / 60.0
        if elapsed_minutes < self.config.max_duration_minutes:
            return False

        # Calculate current profit in pips
        is_buy = trade.direction.upper() == "BUY"
        if is_buy:
            profit_pips = (current_price - trade.entry_price) / pip_size
        else:
            profit_pips = (trade.entry_price - current_price) / pip_size

        if profit_pips < self.config.min_profit_pips:
            logger.info(
                "TimeBasedExit: trade %s open %.1f min, profit=%.1f pips < threshold %.1f — closing",
                trade.trade_id, elapsed_minutes, profit_pips, self.config.min_profit_pips,
            )
            return True

        return False


class TradeManager:
    """
    Orchestrates the full trade lifecycle:
    open → break-even → partial close → trailing stop → time-based exit → grid → close.

    Exit priority per tick
    ----------------------
    1. SL hit (using candle high/low, not just close)
    2. TP hit (using candle high/low)
    3. Time-based exit (stagnant trade too long)
    4. Break-even SL move (once, when profit >= trigger_pips)
    5. Partial close (once, when TP% reached)
    6. Trailing stop update (continuous)
    """

    def __init__(
        self,
        partial_config: Optional[PartialCloseConfig] = None,
        trailing_config: Optional[TrailingConfig] = None,
        grid_config: Optional[GridConfig] = None,
        break_even_config: Optional[BreakEvenConfig] = None,
        time_exit_config: Optional[TimeBasedExitConfig] = None,
        pip_value: float = 10.0,
        pip_size: float = 0.0001,
    ) -> None:
        self.pip_value = pip_value
        self.pip_size = pip_size
        self._partial = PartialCloseManager(partial_config or PartialCloseConfig())
        self._trailing = TrailingStopManager(trailing_config or TrailingConfig())
        self._grid = GridManager(grid_config or GridConfig())
        self._break_even = BreakEvenManager(break_even_config or BreakEvenConfig())
        self._time_exit = TimeBasedExitManager(time_exit_config or TimeBasedExitConfig())
        self._trades: Dict[str, TradeRecord] = {}
        self._closed_trades: List[TradeRecord] = []

        # Running aggregates — updated in _close_trade() for O(1) stats queries
        self._total_pnl_sum: float = 0.0
        self._wins: int = 0
        self._gross_win: float = 0.0
        self._gross_loss: float = 0.0

    def open_trade(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        sl: float,
        tp: float,
        lot_size: float,
        entry_mode: str = "BREAKOUT",
        grid_level: int = 0,
        comment: str = "",
    ) -> TradeRecord:
        trade = TradeRecord(
            trade_id=str(uuid.uuid4())[:8],
            symbol=symbol,
            direction=direction,
            lot_size=lot_size,
            entry_price=entry_price,
            sl=sl,
            tp=tp,
            entry_mode=entry_mode,
            grid_level=grid_level,
            comment=comment,
        )
        self._trades[trade.trade_id] = trade
        logger.info(
            "Trade opened %s: %s %s lot=%.2f @%.5f SL=%.5f TP=%.5f",
            trade.trade_id, direction, symbol, lot_size, entry_price, sl, tp
        )
        return trade

    def update_trade(
        self,
        trade_id: str,
        current_price: float,
        atr: float = 0.0,
        candle_high: Optional[float] = None,
        candle_low: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Update a trade with current price, apply all exit and management logic.

        Parameters
        ----------
        trade_id      : trade to update
        current_price : current bid/ask price (close of last candle)
        atr           : current ATR value (used by trailing stop)
        candle_high   : candle high — used for SL/TP check on SELL trades.
                        Falls back to current_price if not provided.
        candle_low    : candle low — used for SL/TP check on BUY trades.
                        Falls back to current_price if not provided.

        Using candle H/L prevents "walking through" SL on a large spike candle
        where close is beyond SL but within the bar range.
        """
        trade = self._trades.get(trade_id)
        if not trade or not trade.is_open:
            return {}

        # Use candle extremes when available for accurate SL/TP detection
        low_price  = candle_low  if candle_low  is not None else current_price
        high_price = candle_high if candle_high is not None else current_price

        actions: Dict[str, Any] = {}

        # ── 1. SL / TP check (candle H/L based) ──────────────────────────── #
        if self._sl_hit_candle(trade, low_price, high_price):
            self._close_trade(trade, trade.sl, "SL")
            actions["closed"] = "SL"
            return actions

        if self._tp_hit_candle(trade, low_price, high_price):
            self._close_trade(trade, trade.tp, "TP")
            actions["closed"] = "TP"
            return actions

        # ── 2. Time-based exit ────────────────────────────────────────────── #
        if self._time_exit.should_close(trade, current_price, self.pip_size):
            self._close_trade(trade, current_price, "TIME_EXIT")
            actions["closed"] = "TIME_EXIT"
            return actions

        # ── 3. Break-even ─────────────────────────────────────────────────── #
        be_sl = self._break_even.check_and_move(trade, current_price)
        if be_sl is not None:
            actions["break_even_sl"] = be_sl

        # ── 4. Partial close ──────────────────────────────────────────────── #
        closed_lots = self._partial.check_and_close(trade, current_price)
        if closed_lots is not None:
            actions["partial_close"] = closed_lots

        # ── 5. Trailing stop ──────────────────────────────────────────────── #
        new_sl = self._trailing.update(trade, current_price, atr)
        if new_sl is not None:
            trade.sl = new_sl
            actions["trailing_sl"] = new_sl

        return actions

    def close_trade(self, trade_id: str, price: float, reason: str = "MANUAL") -> Optional[TradeRecord]:
        trade = self._trades.get(trade_id)
        if trade and trade.is_open:
            self._close_trade(trade, price, reason)
            return trade
        return None

    def get_open_trades(self) -> List[TradeRecord]:
        return [t for t in self._trades.values() if t.is_open]

    def get_closed_trades(self) -> List[TradeRecord]:
        return list(reversed(self._closed_trades))

    def get_trade(self, trade_id: str) -> Optional[TradeRecord]:
        return self._trades.get(trade_id)

    def total_pnl(self) -> float:
        return self._total_pnl_sum

    def win_rate(self) -> float:
        total = len(self._closed_trades)
        if total == 0:
            return 0.0
        return round(self._wins / total * 100, 1)

    def profit_factor(self) -> float:
        if self._gross_loss == 0:
            return 0.0 if self._gross_win == 0 else 999.9
        return round(self._gross_win / self._gross_loss, 2)

    # ------------------------------------------------------------------ #
    #  Internals                                                           #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _sl_hit(trade: TradeRecord, price: float) -> bool:
        """Legacy single-price SL check (used when no candle data available)."""
        if trade.direction.upper() == "BUY":
            return price <= trade.sl
        return price >= trade.sl

    @staticmethod
    def _tp_hit(trade: TradeRecord, price: float) -> bool:
        """Legacy single-price TP check (used when no candle data available)."""
        if trade.direction.upper() == "BUY":
            return price >= trade.tp
        return price <= trade.tp

    @staticmethod
    def _sl_hit_candle(trade: TradeRecord, candle_low: float, candle_high: float) -> bool:
        """
        SL check using candle extremes.
        BUY  trades: SL hit if candle LOW  touched or crossed below SL.
        SELL trades: SL hit if candle HIGH touched or crossed above SL.
        """
        if trade.direction.upper() == "BUY":
            return candle_low <= trade.sl
        return candle_high >= trade.sl

    @staticmethod
    def _tp_hit_candle(trade: TradeRecord, candle_low: float, candle_high: float) -> bool:
        """
        TP check using candle extremes.
        BUY  trades: TP hit if candle HIGH touched or crossed above TP.
        SELL trades: TP hit if candle LOW  touched or crossed below TP.
        """
        if trade.direction.upper() == "BUY":
            return candle_high >= trade.tp
        return candle_low <= trade.tp

    def _close_trade(self, trade: TradeRecord, price: float, reason: str) -> None:
        trade.close_price = price
        trade.close_time = time.time()
        trade.status = TradeStatus.CLOSED
        trade.pnl = trade.calculate_pnl(price, self.pip_value, self.pip_size)
        trade.comment = f"Closed by {reason}"
        self._closed_trades.append(trade)
        if trade.trade_id in self._trades:
            del self._trades[trade.trade_id]

        # Update running aggregates
        self._total_pnl_sum = round(self._total_pnl_sum + trade.pnl, 2)
        if trade.pnl > 0:
            self._wins += 1
            self._gross_win += trade.pnl
        elif trade.pnl < 0:
            self._gross_loss += abs(trade.pnl)

        logger.info(
            "Trade closed %s @%.5f | PnL=%.2f | Reason=%s",
            trade.trade_id, price, trade.pnl, reason
        )
