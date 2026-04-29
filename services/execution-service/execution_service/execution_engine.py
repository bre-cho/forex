"""Execution engine — orchestrates order routing and account sync."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any, Dict, List, Optional, Union

from .account_sync import AccountSync
from .order_router import OrderRouter
from .providers.base import BrokerProvider, ExecutionCommand, OrderRequest, OrderResult, PreExecutionContext
from trading_core.runtime.pre_execution_gate import PreExecutionGate, hash_gate_context
from trading_core.runtime.frozen_context_contract import validate_frozen_context_bindings

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
        mark_submitting_hook=None,
        enqueue_unknown_hook=None,
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
        # P0.4 hooks: async callables invoked at SUBMITTING and UNKNOWN
        # mark_submitting_hook(bot_instance_id, idempotency_key) -> None
        # enqueue_unknown_hook(bot_instance_id, idempotency_key, signal_id, payload) -> None
        self._mark_submitting_hook = mark_submitting_hook
        self._enqueue_unknown_hook = enqueue_unknown_hook

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

    def _enforce_live_receipt_contract(
        self,
        *,
        result: OrderResult,
        request: OrderRequest,
        context: Optional[PreExecutionContext],
    ) -> OrderResult:
        if result.raw_response is None:
            result.raw_response = {}

        if not result.raw_response_hash and result.raw_response:
            payload = json.dumps(result.raw_response, sort_keys=True, separators=(",", ":"), default=str)
            result.raw_response_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()

        if not result.client_order_id:
            result.client_order_id = str(getattr(request, "client_order_id", "") or getattr(request, "idempotency_key", "") or "") or None
        if not result.account_id and context is not None:
            result.account_id = str(getattr(context, "account_id", "") or "") or None

        if not result.success:
            return result

        missing: list[str] = []
        if str(result.submit_status or "").upper() != "ACKED":
            missing.append("submit_status")
        if str(result.fill_status or "").upper() not in {"FILLED", "PARTIAL"}:
            missing.append("fill_status")
        if not (str(result.broker_order_id or "") or str(result.broker_position_id or "")):
            missing.append("broker_order_or_position_id")
        if not str(result.account_id or ""):
            missing.append("account_id")
        if not str(result.raw_response_hash or ""):
            missing.append("raw_response_hash")

        if missing:
            return OrderResult(
                order_id="",
                symbol=request.symbol,
                side=request.side,
                volume=request.volume,
                fill_price=float(request.price or 0.0),
                commission=0.0,
                success=False,
                error_message=f"invalid_live_execution_receipt:{','.join(missing)}",
                client_order_id=result.client_order_id,
                broker_order_id=result.broker_order_id,
                submit_status="UNKNOWN",
                fill_status="UNKNOWN",
                account_id=result.account_id,
                raw_response_hash=result.raw_response_hash,
                raw_response=result.raw_response,
            )
        return result

    async def place_order(self, payload: Union[OrderRequest, ExecutionCommand]) -> OrderResult:
        ctx: Optional[PreExecutionContext] = None
        if isinstance(payload, ExecutionCommand):
            request = payload.request
            ctx = payload.pre_execution_context
            if self._runtime_mode == "live":
                if not bool(getattr(self._provider, "supports_client_order_id", False)):
                    return OrderResult(
                        order_id="",
                        symbol=request.symbol,
                        side=request.side,
                        volume=request.volume,
                        fill_price=float(request.price or 0.0),
                        commission=0.0,
                        success=False,
                        error_message="execution_gate_blocked:provider_client_order_id_unsupported",
                    )
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
                binding = validate_frozen_context_bindings(
                    request=request,
                    context=ctx,
                    provider_name=self._provider_name,
                )
                if not binding.ok:
                    return OrderResult(
                        order_id="",
                        symbol=request.symbol,
                        side=request.side,
                        volume=request.volume,
                        fill_price=float(request.price or 0.0),
                        commission=0.0,
                        success=False,
                        error_message=f"execution_gate_blocked:{binding.reason}",
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

        # P0.4 — Persist SUBMITTING before calling broker.
        # If the process dies after this line but before receipt, DB shows SUBMITTING
        # and the unknown-order daemon will catch it during next poll.
        _bot_id = str(getattr(ctx, "bot_instance_id", "") or "") if ctx else ""
        _idem_key = str(getattr(request, "idempotency_key", "") or getattr(request, "client_order_id", "") or "")
        _signal_id = str(getattr(getattr(payload, "intent", None) or {}, "signal_id", "") or "") if isinstance(payload, ExecutionCommand) else ""
        if self._runtime_mode == "live" and _bot_id and _idem_key and callable(self._mark_submitting_hook):
            try:
                await self._mark_submitting_hook(_bot_id, _idem_key)
            except Exception as _hook_exc:
                logger.warning("mark_submitting_hook failed idem=%s: %s", _idem_key, _hook_exc)

        started = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                self._router.route(self._provider_name, request),
                timeout=self._submit_timeout_seconds,
            )
        except asyncio.TimeoutError:
            _result = OrderResult(
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
            # P0.4 — Enqueue for reconciliation after timeout
            if self._runtime_mode == "live" and _bot_id and _idem_key and callable(self._enqueue_unknown_hook):
                try:
                    await self._enqueue_unknown_hook(
                        _bot_id, _idem_key, _signal_id,
                        {"reason": "broker_submit_timeout", "symbol": request.symbol, "idempotency_key": _idem_key},
                    )
                except Exception as _q_exc:
                    logger.warning("enqueue_unknown_hook failed idem=%s: %s", _idem_key, _q_exc)
            return _result
        except Exception as exc:
            _result = OrderResult(
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
            # P0.4 — Enqueue for reconciliation after broker error
            if self._runtime_mode == "live" and _bot_id and _idem_key and callable(self._enqueue_unknown_hook):
                try:
                    await self._enqueue_unknown_hook(
                        _bot_id, _idem_key, _signal_id,
                        {"reason": f"broker_submit_error:{exc}", "symbol": request.symbol, "idempotency_key": _idem_key},
                    )
                except Exception as _q_exc:
                    logger.warning("enqueue_unknown_hook failed idem=%s: %s", _idem_key, _q_exc)
            return _result
        if result.raw_response is None:
            result.raw_response = {}
        result.raw_response.setdefault("latency_ms", round((time.perf_counter() - started) * 1000.0, 2))
        if self._runtime_mode == "live":
            result = self._enforce_live_receipt_contract(result=result, request=request, context=ctx)
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
