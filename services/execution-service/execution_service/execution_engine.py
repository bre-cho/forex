"""Execution engine — orchestrates order routing and account sync."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Union

from .account_sync import AccountSync
from .order_router import OrderRouter
from .providers.base import BrokerProvider, ExecutionCommand, OrderRequest, OrderResult, PreExecutionContext
from trading_core.runtime.pre_execution_gate import PreExecutionGate, hash_gate_context

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
        verify_idempotency_reservation=None,
        submit_timeout_seconds: float = 10.0,
    ) -> None:
        self._provider = provider
        self._provider_name = provider_name
        self._router = OrderRouter()
        self._account_sync: Optional[AccountSync] = None
        self._last_account_info: Dict[str, Any] = {}
        self._sync_interval = sync_interval
        self._runtime_mode = str(runtime_mode or "paper").lower()
        self._gate = PreExecutionGate(gate_policy or {})
        self._verify_idempotency_reservation = verify_idempotency_reservation
        self._submit_timeout_seconds = float(submit_timeout_seconds)

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
                if self._verify_idempotency_reservation is None:
                    return OrderResult(
                        order_id="",
                        symbol=request.symbol,
                        side=request.side,
                        volume=request.volume,
                        fill_price=float(request.price or 0.0),
                        commission=0.0,
                        success=False,
                        error_message="execution_gate_blocked:missing_idempotency_verifier",
                    )
                try:
                    reservation_exists = bool(
                        await self._verify_idempotency_reservation(
                            ctx.bot_instance_id,
                            payload.idempotency_key,
                            payload.brain_cycle_id or None,
                        )
                    )
                except Exception as exc:
                    return OrderResult(
                        order_id="",
                        symbol=request.symbol,
                        side=request.side,
                        volume=request.volume,
                        fill_price=float(request.price or 0.0),
                        commission=0.0,
                        success=False,
                        error_message=f"execution_gate_blocked:idempotency_verification_failed:{exc}",
                    )
                if not reservation_exists:
                    return OrderResult(
                        order_id="",
                        symbol=request.symbol,
                        side=request.side,
                        volume=request.volume,
                        fill_price=float(request.price or 0.0),
                        commission=0.0,
                        success=False,
                        error_message="execution_gate_blocked:missing_idempotency_reservation",
                    )
                if not getattr(ctx, "context_hash", ""):
                    return OrderResult(
                        order_id="",
                        symbol=request.symbol,
                        side=request.side,
                        volume=request.volume,
                        fill_price=float(request.price or 0.0),
                        commission=0.0,
                        success=False,
                        error_message="execution_gate_blocked:missing_gate_context_hash",
                    )
                gate_ctx = dict(getattr(ctx, "gate_context", {}) or {})
                if not gate_ctx:
                    return OrderResult(
                        order_id="",
                        symbol=request.symbol,
                        side=request.side,
                        volume=request.volume,
                        fill_price=float(request.price or 0.0),
                        commission=0.0,
                        success=False,
                        error_message="execution_gate_blocked:missing_frozen_gate_context",
                    )
                try:
                    computed_hash = hash_gate_context(gate_ctx)
                except Exception as exc:
                    return OrderResult(
                        order_id="",
                        symbol=request.symbol,
                        side=request.side,
                        volume=request.volume,
                        fill_price=float(request.price or 0.0),
                        commission=0.0,
                        success=False,
                        error_message=f"execution_gate_blocked:gate_context_hash_failed:{exc}",
                    )
                if computed_hash != str(ctx.context_hash):
                    return OrderResult(
                        order_id="",
                        symbol=request.symbol,
                        side=request.side,
                        volume=request.volume,
                        fill_price=float(request.price or 0.0),
                        commission=0.0,
                        success=False,
                        error_message="execution_gate_blocked:gate_context_hash_mismatch",
                    )
                # Revalidate critical request bindings against frozen gate context
                frozen_volume = float(gate_ctx.get("requested_volume", 0.0) or 0.0)
                if frozen_volume > 0 and abs(frozen_volume - float(request.volume or 0.0)) > 1e-9:
                    return OrderResult(
                        order_id="",
                        symbol=request.symbol,
                        side=request.side,
                        volume=request.volume,
                        fill_price=float(request.price or 0.0),
                        commission=0.0,
                        success=False,
                        error_message="execution_gate_blocked:request_volume_context_mismatch",
                    )
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
        started = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                self._router.route(self._provider_name, request),
                timeout=self._submit_timeout_seconds,
            )
        except TimeoutError:
            return OrderResult(
                order_id="",
                symbol=request.symbol,
                side=request.side,
                volume=request.volume,
                fill_price=float(request.price or 0.0),
                commission=0.0,
                success=False,
                error_message="broker_submit_timeout",
                submit_status="UNKNOWN",
                fill_status="UNKNOWN",
                raw_response={"latency_ms": round((time.perf_counter() - started) * 1000.0, 2)},
            )
        except Exception as exc:
            return OrderResult(
                order_id="",
                symbol=request.symbol,
                side=request.side,
                volume=request.volume,
                fill_price=float(request.price or 0.0),
                commission=0.0,
                success=False,
                error_message=f"broker_submit_error:{exc}",
                submit_status="UNKNOWN",
                fill_status="UNKNOWN",
                raw_response={"latency_ms": round((time.perf_counter() - started) * 1000.0, 2)},
            )
        if result.raw_response is None:
            result.raw_response = {}
        result.raw_response.setdefault("latency_ms", round((time.perf_counter() - started) * 1000.0, 2))
        return result

    async def close_position(self, position_id: str) -> OrderResult:
        return await self._router.close(self._provider_name, position_id)

    async def get_open_positions(self) -> List[Dict[str, Any]]:
        return await self._provider.get_open_positions()

    async def get_trade_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        return await self._provider.get_trade_history(limit=limit)

    @property
    def account_info(self) -> Dict[str, Any]:
        return self._last_account_info
