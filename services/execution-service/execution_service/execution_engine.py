"""Execution engine — orchestrates order routing and account sync."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Union

from .account_sync import AccountSync
from .order_router import OrderRouter
from .providers.base import BrokerProvider, ExecutionCommand, OrderRequest, OrderResult, PreExecutionContext
from trading_core.runtime.pre_execution_gate import PreExecutionGate

logger = logging.getLogger(__name__)


class ExecutionEngine:
    """
    Top-level execution engine.

    Manages provider lifecycle, order routing, and account synchronisation
    for a single bot instance.
    """

    def __init__(
        self,
        provider: BrokerProvider,
        provider_name: str = "default",
        sync_interval: float = 30.0,
        runtime_mode: str = "paper",
        gate_policy: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._provider = provider
        self._provider_name = provider_name
        self._router = OrderRouter()
        self._account_sync: Optional[AccountSync] = None
        self._last_account_info: Dict[str, Any] = {}
        self._sync_interval = sync_interval
        self._runtime_mode = str(runtime_mode or "paper").lower()
        self._gate = PreExecutionGate(gate_policy or {})

    async def start(self) -> None:
        await self._provider.connect()
        self._router.register(self._provider_name, self._provider)
        self._account_sync = AccountSync(
            provider=self._provider,
            on_update=self._on_account_update,
            interval_seconds=self._sync_interval,
        )
        await self._account_sync.start()
        logger.info("ExecutionEngine started: provider=%s", self._provider_name)

    async def stop(self) -> None:
        if self._account_sync:
            await self._account_sync.stop()
        await self._provider.disconnect()
        logger.info("ExecutionEngine stopped")

    def _on_account_update(self, info: Dict[str, Any]) -> None:
        self._last_account_info = info

    async def place_order(self, payload: Union[OrderRequest, ExecutionCommand]) -> OrderResult:
        if isinstance(payload, ExecutionCommand):
            request = payload.request
            ctx = payload.pre_execution_context
            if self._runtime_mode == "live":
                if not payload.brain_cycle_id:
                    return OrderResult(
                        order_id="",
                        symbol=request.symbol,
                        side=request.side,
                        volume=request.volume,
                        fill_price=float(request.price or 0.0),
                        commission=0.0,
                        success=False,
                        error_message="execution_gate_blocked:missing_brain_cycle_id",
                    )
                if not payload.idempotency_key:
                    return OrderResult(
                        order_id="",
                        symbol=request.symbol,
                        side=request.side,
                        volume=request.volume,
                        fill_price=float(request.price or 0.0),
                        commission=0.0,
                        success=False,
                        error_message="execution_gate_blocked:missing_idempotency_key",
                    )
                if ctx is None:
                    return OrderResult(
                        order_id="",
                        symbol=request.symbol,
                        side=request.side,
                        volume=request.volume,
                        fill_price=float(request.price or 0.0),
                        commission=0.0,
                        success=False,
                        error_message="execution_gate_blocked:missing_pre_execution_context",
                    )
                gate_ctx = {
                    "provider_mode": ctx.provider_mode,
                    "runtime_mode": ctx.runtime_mode,
                    "broker_connected": ctx.broker_connected,
                    "market_data_ok": ctx.market_data_ok,
                    "data_age_seconds": ctx.data_age_seconds,
                    "daily_profit_amount": ctx.daily_profit_amount,
                    "daily_loss_pct": ctx.daily_loss_pct,
                    "consecutive_losses": ctx.consecutive_losses,
                    "spread_pips": ctx.spread_pips,
                    "confidence": ctx.confidence,
                    "rr": ctx.rr,
                    "open_positions": ctx.open_positions,
                    "idempotency_exists": False,
                    "kill_switch": bool(ctx.kill_switch or ctx.daily_locked),
                }
            else:
                gate_ctx = {
                    "provider_mode": str(getattr(self._provider, "mode", "stub")),
                    "runtime_mode": self._runtime_mode,
                    "broker_connected": bool(getattr(self._provider, "is_connected", False)),
                    "market_data_ok": True,
                    "data_age_seconds": 0,
                    "daily_profit_amount": 0,
                    "daily_loss_pct": 0,
                    "consecutive_losses": 0,
                    "spread_pips": 0,
                    "confidence": 1,
                    "rr": 2,
                    "open_positions": 0,
                    "idempotency_exists": False,
                    "kill_switch": False,
                }
        else:
            request = payload
            if self._runtime_mode == "live":
                return OrderResult(
                    order_id="",
                    symbol=request.symbol,
                    side=request.side,
                    volume=request.volume,
                    fill_price=float(request.price or 0.0),
                    commission=0.0,
                    success=False,
                    error_message="execution_gate_blocked:execution_command_required",
                )
            gate_ctx = {
                "provider_mode": str(getattr(self._provider, "mode", "stub")),
                "runtime_mode": self._runtime_mode,
                "broker_connected": bool(getattr(self._provider, "is_connected", False)),
                "market_data_ok": True,
                "data_age_seconds": 0,
                "daily_profit_amount": 0,
                "daily_loss_pct": 0,
                "consecutive_losses": 0,
                "spread_pips": 0,
                "confidence": 1,
                "rr": 2,
                "open_positions": 0,
                "idempotency_exists": False,
                "kill_switch": False,
            }

        gate_result = self._gate.evaluate(gate_ctx)
        if gate_result.action != "ALLOW":
            return OrderResult(
                order_id="",
                symbol=request.symbol,
                side=request.side,
                volume=request.volume,
                fill_price=float(request.price or 0.0),
                commission=0.0,
                success=False,
                error_message=f"execution_gate_blocked:{gate_result.reason}",
            )
        return await self._router.route(self._provider_name, request)

    async def close_position(self, position_id: str) -> OrderResult:
        return await self._router.close(self._provider_name, position_id)

    async def get_open_positions(self) -> List[Dict[str, Any]]:
        return await self._provider.get_open_positions()

    async def get_trade_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        return await self._provider.get_trade_history(limit=limit)

    @property
    def account_info(self) -> Dict[str, Any]:
        return self._last_account_info
