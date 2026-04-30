from __future__ import annotations

import asyncio

import pytest

from execution_service.execution_engine import ExecutionEngine
from execution_service.providers.base import ExecutionCommand, OrderRequest, PreExecutionContext
from trading_core.runtime.pre_execution_gate import hash_gate_context


class _SlowProvider:
    mode = "live"
    is_connected = True

    async def connect(self):
        return None

    async def disconnect(self):
        return None

    async def get_account_info(self):
        return type("Account", (), {"equity": 1000.0})()

    async def get_candles(self, symbol: str, timeframe: str, limit: int = 200):
        return None

    async def place_order(self, request: OrderRequest):
        await asyncio.sleep(0.2)

    async def close_position(self, position_id: str):
        return None

    async def get_open_positions(self):
        return []

    async def get_trade_history(self, limit: int = 100):
        return []


@pytest.mark.asyncio
async def test_execution_engine_timeout_returns_unknown_status() -> None:
    provider = _SlowProvider()
    engine = ExecutionEngine(provider=provider, runtime_mode="paper", submit_timeout_seconds=0.01)
    engine._router.register("default", provider)

    result = await engine.place_order(
        OrderRequest(symbol="EURUSD", side="buy", volume=0.01, order_type="market")
    )

    assert result.success is False
    assert result.submit_status == "UNKNOWN"
    assert result.fill_status == "UNKNOWN"
    assert "timeout" in str(result.error_message)


def _live_command(idem: str = "idem-live-timeout") -> ExecutionCommand:
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
        "quote_id": "q-1",
        "quote_timestamp": 1000.0,
        "broker_server_time": 1001.0,
        "instrument_spec_hash": "spec_hash_1",
        "broker_snapshot_hash": "broker_snap_hash_1",
        "broker_account_snapshot_hash": "acct_snap_hash_1",
        "risk_context_hash": "risk_hash_1",
        "idempotency_key": idem,
        "approved_volume": 0.01,
    }
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
        idempotency_key=idem,
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
    )
    return ExecutionCommand(
        request=OrderRequest(
            symbol="EURUSD",
            side="buy",
            volume=0.01,
            order_type="market",
            price=1.1000,
            client_order_id=idem,
            idempotency_key=idem,
        ),
        intent={"symbol": "EURUSD", "side": "buy", "lot_size": 0.01, "signal_id": "sig-1"},
        pre_execution_context=ctx,
        idempotency_key=idem,
        brain_cycle_id="cycle-1",
    )


@pytest.mark.asyncio
async def test_live_blocks_when_mark_submitting_hook_fails() -> None:
    provider = _SlowProvider()
    provider.supports_client_order_id = True

    async def _verify(*args, **kwargs):
        return True

    async def _mark_fail(*args, **kwargs):
        raise RuntimeError("db_write_failed")

    engine = ExecutionEngine(
        provider=provider,
        provider_name="ctrader",
        runtime_mode="live",
        submit_timeout_seconds=0.01,
        verify_idempotency_reservation=_verify,
        mark_submitting_hook=_mark_fail,
    )
    engine._router.register("ctrader", provider)

    result = await engine.place_order(_live_command("idem-live-fail-mark"))
    assert result.success is False
    assert "mark_submitting_failed" in str(result.error_message)


@pytest.mark.asyncio
async def test_live_timeout_reports_critical_when_enqueue_unknown_fails() -> None:
    provider = _SlowProvider()
    provider.supports_client_order_id = True

    async def _verify(*args, **kwargs):
        return True

    async def _mark_ok(*args, **kwargs):
        return None

    async def _enqueue_fail(*args, **kwargs):
        raise RuntimeError("queue_down")

    engine = ExecutionEngine(
        provider=provider,
        provider_name="ctrader",
        runtime_mode="live",
        submit_timeout_seconds=0.01,
        verify_idempotency_reservation=_verify,
        mark_submitting_hook=_mark_ok,
        enqueue_unknown_hook=_enqueue_fail,
    )
    engine._router.register("ctrader", provider)

    result = await engine.place_order(_live_command("idem-live-timeout-fail-queue"))
    assert result.success is False
    assert "critical_unknown_enqueue_failed" in str(result.error_message)
