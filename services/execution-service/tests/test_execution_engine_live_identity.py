from __future__ import annotations

import pytest

from execution_service.execution_engine import ExecutionEngine
from execution_service.providers.base import ExecutionCommand, OrderRequest, PreExecutionContext, OrderResult
from trading_core.runtime.pre_execution_gate import hash_gate_context


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
            fill_price=1.1001,
            commission=0.0,
            success=True,
            submit_status="ACKED",
            fill_status="FILLED",
            broker_order_id="bo-1",
            account_id="acc-1",
            raw_response_hash="hash-1",
            raw_response={"ok": True},
        )

    async def close_position(self, position_id: str):
        return None

    async def get_open_positions(self):
        return []

    async def get_trade_history(self, limit: int = 100):
        return []


def _command() -> ExecutionCommand:
    gate_context = {
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
        "idempotency_key": "idem-1",
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
        intent={"symbol": "EURUSD", "side": "buy", "lot_size": 0.01},
        pre_execution_context=ctx,
        idempotency_key="idem-1",
        brain_cycle_id="cycle-1",
    )


@pytest.mark.asyncio
async def test_live_uses_broker_identity_for_frozen_context() -> None:
    provider = _LiveProvider()

    async def _verify(*args, **kwargs):
        return True

    engine = ExecutionEngine(
        provider=provider,
        provider_name="ctrader",
        runtime_mode="live",
        verify_idempotency_reservation=_verify,
    )
    engine._router.register("ctrader", provider)

    result = await engine.place_order(_command())
    assert result.success is True


@pytest.mark.asyncio
async def test_live_invalid_receipt_is_downgraded_to_unknown() -> None:
    class _BadProvider(_LiveProvider):
        async def place_order(self, request: OrderRequest):
            return OrderResult(
                order_id="bo-1",
                symbol=request.symbol,
                side=request.side,
                volume=request.volume,
                fill_price=1.1001,
                commission=0.0,
                success=True,
                submit_status="ACKED",
                fill_status="FILLED",
                broker_order_id="",
                broker_position_id="",
                account_id="",
                raw_response_hash=None,
                raw_response={"ok": True},
            )

    provider = _BadProvider()

    async def _verify(*args, **kwargs):
        return True

    engine = ExecutionEngine(
        provider=provider,
        provider_name="ctrader",
        runtime_mode="live",
        verify_idempotency_reservation=_verify,
    )
    engine._router.register("ctrader", provider)

    result = await engine.place_order(_command())
    assert result.success is False
    assert result.submit_status == "UNKNOWN"
    assert "invalid_live_execution_receipt" in str(result.error_message)
