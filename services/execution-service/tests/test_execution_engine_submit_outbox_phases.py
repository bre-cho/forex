from __future__ import annotations

import pytest

from execution_service.execution_engine import ExecutionEngine
from execution_service.providers.base import ExecutionCommand, OrderRequest, OrderResult, PreExecutionContext
from trading_core.runtime.pre_execution_gate import hash_gate_context, build_frozen_context_id, sign_gate_context


class _LiveProvider:
    mode = "live"
    is_connected = True
    provider_name = "ctrader"
    supports_client_order_id = True

    async def connect(self):
        return None

    async def disconnect(self):
        return None

    async def get_account_info(self):
        return type("Account", (), {"equity": 1000.0})()

    async def get_candles(self, symbol: str, timeframe: str, limit: int = 200):
        return None

    async def place_order(self, request: OrderRequest):
        return OrderResult(
            order_id="bo-1",
            symbol=request.symbol,
            side=request.side,
            volume=request.volume,
            fill_price=1.1,
            commission=0.0,
            success=True,
            submit_status="ACKED",
            fill_status="FILLED",
            broker_order_id="bo-1",
            raw_response_hash="h",
            raw_response={"ok": True},
        )

    async def close_position(self, position_id: str):
        return None

    async def get_open_positions(self):
        return []

    async def get_trade_history(self, limit: int = 100):
        return []


def _live_command() -> ExecutionCommand:
    gate_context = {
        "schema_version": "gate_context_v2",
        "provider_mode": "live",
        "runtime_mode": "live",
        "broker_connected": True,
        "market_data_ok": True,
        "data_age_seconds": 0.1,
        "spread_pips": 0.2,
        "confidence": 0.8,
        "rr": 2.0,
        "open_positions": 0,
        "daily_profit_amount": 0.0,
        "daily_loss_pct": 0.0,
        "consecutive_losses": 0,
        "daily_locked": False,
        "kill_switch": False,
        "idempotency_exists": False,
        "requested_volume": 0.01,
        "symbol": "EURUSD",
        "side": "buy",
        "account_id": "acc-1",
        "broker_name": "ctrader",
        "policy_version": "v1",
        "policy_version_id": "v1",
        "policy_status": "active",
        "policy_hash": "policy_hash_1",
        "idempotency_key": "idem-1",
        "approved_volume": 0.01,
        "quote_id": "q-1",
        "quote_timestamp": 1000.0,
        "broker_server_time": 1001.0,
        "instrument_spec_hash": "spec_hash_1",
        "broker_snapshot_hash": "broker_snap_hash_1",
        "broker_account_snapshot_hash": "acct_snap_hash_1",
        "risk_context_hash": "risk_hash_1",
        "unknown_orders_unresolved": False,
        "stop_loss": 1.0900,
        "context_signature": "",
        "frozen_context_id": "",
    }
    _fid = build_frozen_context_id(gate_context)
    _sig = sign_gate_context(gate_context, secret="test_secret") or "test_sig"
    gate_context["frozen_context_id"] = _fid
    gate_context["context_signature"] = _sig
    ctx = PreExecutionContext(
        bot_instance_id="bot-1",
        runtime_mode="live",
        provider_mode="live",
        broker_connected=True,
        market_data_ok=True,
        data_age_seconds=0.1,
        spread_pips=0.2,
        confidence=0.8,
        rr=2.0,
        open_positions=0,
        daily_profit_amount=0.0,
        daily_loss_pct=0.0,
        consecutive_losses=0,
        daily_locked=False,
        kill_switch=False,
        idempotency_key="idem-1",
        brain_cycle_id="cycle-1",
        account_id="acc-1",
        broker_name="ctrader",
        order_type="market",
        entry_price=1.1000,
        stop_loss=1.0900,
        take_profit=1.1200,
        policy_version="v1",
        gate_context=gate_context,
        context_hash=hash_gate_context(gate_context),
        frozen_context_id=_fid,
        context_signature=_sig,
    )
    return ExecutionCommand(
        request=OrderRequest(
            symbol="EURUSD",
            side="buy",
            volume=0.01,
            order_type="market",
            price=1.1000,
            client_order_id="idem-1",
            idempotency_key="idem-1",
        ),
        intent={"signal_id": "sig-1"},
        pre_execution_context=ctx,
        idempotency_key="idem-1",
        brain_cycle_id="cycle-1",
    )


@pytest.mark.asyncio
async def test_execution_engine_marks_submit_outbox_phases() -> None:
    provider = _LiveProvider()
    phases: list[str] = []

    async def _verify(*args, **kwargs):
        return True

    async def _mark_submitting(*args, **kwargs):
        return None

    async def _mark_phase(*args, **kwargs):
        phases.append(str(args[2]))

    engine = ExecutionEngine(
        provider=provider,
        provider_name="ctrader",
        runtime_mode="live",
        verify_idempotency_reservation=_verify,
        mark_submitting_hook=_mark_submitting,
        mark_submit_phase_hook=_mark_phase,
    )
    engine._router.register("ctrader", provider)

    result = await engine.place_order(_live_command())
    assert result.success is True
    assert "BROKER_SEND_STARTED" in phases
    assert "BROKER_SEND_RETURNED" in phases
