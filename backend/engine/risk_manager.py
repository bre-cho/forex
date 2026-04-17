"""
Risk Manager — Lot sizing, martingale, drawdown protection, spread check.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

PIP_VALUE = 0.0001   # default pip size for 5-digit Forex pairs


class LotMode(str, Enum):
    STATIC = "STATIC"
    DYNAMIC_PERCENT = "DYNAMIC_PERCENT"
    LOT_PER_X_BALANCE = "LOT_PER_X_BALANCE"


@dataclass
class RiskConfig:
    lot_mode: LotMode = LotMode.STATIC
    lot_value: float = 0.01          # fixed lot / % / balance-per-lot
    min_lot: float = 0.01
    max_lot: float = 10.0
    lot_step: float = 0.01
    max_account_equity: float = 0.0  # 0 = disabled
    max_daily_dd_pct: float = 5.0    # % of starting day balance
    max_overall_dd_pct: float = 20.0 # % of account peak
    pip_value_per_lot: float = 10.0  # USD per pip per lot (standard lot)
    daily_profit_target: float = 0.0  # lock when daily PnL ≥ target ($, 0=off)
    daily_loss_limit: float = 0.0     # lock when daily PnL ≤ -limit ($, 0=off; overrides dd_pct)


@dataclass
class MartingaleConfig:
    enabled: bool = False
    multiplier: float = 2.0
    max_steps: int = 4


class MartingaleManager:
    def __init__(self, config: MartingaleConfig) -> None:
        self.config = config
        self._consecutive_losses: int = 0
        self._step: int = 0

    def on_trade_result(self, profit: float) -> None:
        if profit < 0:
            self._consecutive_losses += 1
            self._step = min(self._consecutive_losses, self.config.max_steps)
        else:
            self._consecutive_losses = 0
            self._step = 0

    def apply(self, base_lot: float) -> float:
        if not self.config.enabled or self._step == 0:
            return base_lot
        return base_lot * (self.config.multiplier ** self._step)

    @property
    def current_step(self) -> int:
        return self._step

    @property
    def consecutive_losses(self) -> int:
        return self._consecutive_losses


@dataclass
class DrawdownSnapshot:
    balance: float
    equity: float
    timestamp: float


class DrawdownProtection:
    def __init__(self, config: RiskConfig) -> None:
        self.config = config
        self._peak_equity: float = 0.0
        self._day_start_balance: float = 0.0
        self._daily_pnl: float = 0.0
        self._triggered: bool = False
        # Daily profit/loss locks — require manual user reset to clear
        self._profit_locked: bool = False
        self._loss_locked: bool = False
        self._lock_reason: str = ""

    def initialise(self, balance: float, equity: float) -> None:
        self._peak_equity = max(self._peak_equity, equity)
        if self._day_start_balance == 0.0:
            self._day_start_balance = balance

    def update(self, balance: float, equity: float, realised_pnl: float = 0.0) -> None:
        self._peak_equity = max(self._peak_equity, equity)
        self._daily_pnl += realised_pnl
        self._check_daily_locks()

    def _check_daily_locks(self) -> None:
        """Check and activate daily profit / loss locks."""
        if not self._profit_locked and self.config.daily_profit_target > 0:
            if self._daily_pnl >= self.config.daily_profit_target:
                self._profit_locked = True
                self._lock_reason = (
                    f"Daily profit target reached: ${self._daily_pnl:+.2f} ≥ "
                    f"${self.config.daily_profit_target:.2f}"
                )
                logger.warning("DrawdownProtection: PROFIT LOCK — %s", self._lock_reason)

        if not self._loss_locked:
            if self.config.daily_loss_limit > 0:
                if self._daily_pnl <= -self.config.daily_loss_limit:
                    self._loss_locked = True
                    self._lock_reason = (
                        f"Daily loss limit reached: ${self._daily_pnl:+.2f} ≤ "
                        f"-${self.config.daily_loss_limit:.2f}"
                    )
                    logger.warning("DrawdownProtection: LOSS LOCK — %s", self._lock_reason)

    def reset_daily(self, balance: float) -> None:
        self._day_start_balance = balance
        self._daily_pnl = 0.0
        self._triggered = False
        # Daily profit/loss locks persist across day resets — user must reset manually
        # (they are reset via reset_daily_locks())

    def reset_daily_locks(self) -> None:
        """Manual user action — clears daily profit/loss locks."""
        self._profit_locked = False
        self._loss_locked = False
        self._lock_reason = ""
        logger.info("DrawdownProtection: daily locks manually reset by user")

    def is_safe(self, equity: float) -> bool:
        if self._triggered:
            return False

        # Daily profit lock — stop new trades after target reached
        if self._profit_locked:
            return False

        # Daily loss lock — stop new trades after loss limit reached
        if self._loss_locked:
            return False

        # Overall drawdown check
        if self._peak_equity > 0:
            overall_dd_pct = (self._peak_equity - equity) / self._peak_equity * 100
            if overall_dd_pct >= self.config.max_overall_dd_pct:
                logger.warning(
                    "Overall DD limit hit: %.2f%% (limit %.2f%%)",
                    overall_dd_pct,
                    self.config.max_overall_dd_pct,
                )
                self._triggered = True
                return False

        # Daily drawdown check (% based — supplements absolute daily_loss_limit)
        if self._day_start_balance > 0:
            daily_dd_pct = (-self._daily_pnl) / self._day_start_balance * 100
            if daily_dd_pct >= self.config.max_daily_dd_pct:
                logger.warning(
                    "Daily DD limit hit: %.2f%% (limit %.2f%%)",
                    daily_dd_pct,
                    self.config.max_daily_dd_pct,
                )
                self._triggered = True
                return False

        # Max account equity cap
        if self.config.max_account_equity > 0 and equity > self.config.max_account_equity:
            logger.warning(
                "Max account equity cap hit: %.2f (cap %.2f)",
                equity,
                self.config.max_account_equity,
            )
            return False

        return True

    @property
    def daily_pnl(self) -> float:
        return self._daily_pnl

    @property
    def peak_equity(self) -> float:
        return self._peak_equity

    @property
    def triggered(self) -> bool:
        return self._triggered

    @property
    def profit_locked(self) -> bool:
        return self._profit_locked

    @property
    def loss_locked(self) -> bool:
        return self._loss_locked

    @property
    def daily_locked(self) -> bool:
        return self._profit_locked or self._loss_locked

    @property
    def lock_reason(self) -> str:
        return self._lock_reason


class RiskManager:
    """
    Central risk manager: lot sizing + martingale + drawdown protection.
    """

    def __init__(
        self,
        config: Optional[RiskConfig] = None,
        martingale: Optional[MartingaleConfig] = None,
    ) -> None:
        self.config = config or RiskConfig()
        self._martingale = MartingaleManager(martingale or MartingaleConfig())
        self._dd_protection = DrawdownProtection(self.config)
        self._spread_cache: Dict[str, float] = {}

    # ------------------------------------------------------------------ #
    #  Lot Sizing                                                          #
    # ------------------------------------------------------------------ #

    def calculate_lot_size(
        self,
        balance: float,
        equity: float,
        sl_points: float = 100,
        mode: Optional[LotMode] = None,
        value: Optional[float] = None,
    ) -> float:
        mode = mode or self.config.lot_mode
        value = value if value is not None else self.config.lot_value
        sl_points = max(sl_points, 1)

        if mode == LotMode.STATIC:
            lot = value

        elif mode == LotMode.DYNAMIC_PERCENT:
            # value = % risk of balance
            risk_amount = balance * (value / 100.0)
            pip_risk = sl_points * PIP_VALUE
            if pip_risk > 0 and self.config.pip_value_per_lot > 0:
                lot = risk_amount / (pip_risk / PIP_VALUE * self.config.pip_value_per_lot)
            else:
                lot = self.config.min_lot

        elif mode == LotMode.LOT_PER_X_BALANCE:
            # value = balance per 1 lot (e.g. 10000 → 0.1 lot per 1000)
            if value > 0:
                lot = balance / value
            else:
                lot = self.config.min_lot

        else:
            lot = self.config.lot_value

        # Apply martingale
        lot = self._martingale.apply(lot)

        # Clamp and round to lot_step
        lot = max(self.config.min_lot, min(self.config.max_lot, lot))
        step = self.config.lot_step
        lot = round(round(lot / step) * step, 8)
        return lot

    # ------------------------------------------------------------------ #
    #  Drawdown / Safety checks                                           #
    # ------------------------------------------------------------------ #

    def update_equity(
        self, balance: float, equity: float, realised_pnl: float = 0.0
    ) -> None:
        self._dd_protection.initialise(balance, equity)
        self._dd_protection.update(balance, equity, realised_pnl)

    def is_trading_allowed(self, equity: float) -> bool:
        return self._dd_protection.is_safe(equity)

    def reset_daily(self, balance: float) -> None:
        self._dd_protection.reset_daily(balance)

    def reset_daily_locks(self) -> None:
        """Manual user reset of daily profit/loss locks."""
        self._dd_protection.reset_daily_locks()

    def on_trade_closed(self, profit: float) -> None:
        self._martingale.on_trade_result(profit)
        self._dd_protection.update(0, 0, profit)

    # ------------------------------------------------------------------ #
    #  Spread check                                                        #
    # ------------------------------------------------------------------ #

    def update_spread(self, symbol: str, spread_points: float) -> None:
        self._spread_cache[symbol] = spread_points

    def check_spread(self, symbol: str, max_spread: float) -> bool:
        """Returns True if spread is within acceptable limit."""
        current = self._spread_cache.get(symbol, 0.0)
        if current <= 0:
            return True   # unknown spread — allow
        return current <= max_spread

    # ------------------------------------------------------------------ #
    #  Metrics                                                             #
    # ------------------------------------------------------------------ #

    @property
    def martingale_step(self) -> int:
        return self._martingale.current_step

    @property
    def consecutive_losses(self) -> int:
        return self._martingale.consecutive_losses

    @property
    def daily_pnl(self) -> float:
        return self._dd_protection.daily_pnl

    @property
    def peak_equity(self) -> float:
        return self._dd_protection.peak_equity

    @property
    def dd_triggered(self) -> bool:
        return self._dd_protection.triggered

    @property
    def profit_locked(self) -> bool:
        return self._dd_protection.profit_locked

    @property
    def loss_locked(self) -> bool:
        return self._dd_protection.loss_locked

    @property
    def daily_locked(self) -> bool:
        return self._dd_protection.daily_locked

    @property
    def lock_reason(self) -> str:
        return self._dd_protection.lock_reason
