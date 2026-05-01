from __future__ import annotations

import pytest

from execution_service.execution_engine import ExecutionEngine
from execution_service.providers.base import ExecutionCommand, OrderRequest, OrderResult, PreExecutionContext


class _RetryProvider:
    mode = "demo"
    is_connected = True
    provider_name = "default"

    def __init__(self) -> None:
        self.calls = 0

    async def connect(self):
        return None

    async def disconnect(self):
        return None

    async def get_account_info(self):
        return type("Account", (), {"equity": 1000.0})()

    async def get_candles(self, symbol: str, timeframe: str, limit: int = 200):
        return None

    async def place_order(self, request: OrderRequest):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("transient_provider_error")
        return OrderResult(
            order_id="ok-1",
            symbol=request.symbol,
            side=request.side,
            volume=request.volume,
            fill_price=1.1,
            commission=0.0,
            success=True,
            submit_status="ACKED",
            fill_status="FILLED",
            broker_order_id="ok-1",
            raw_response={"provider": "default"},
            raw_response_hash="h",
        )

    async def close_position(self, position_id: str):
        return None

    async def get_open_positions(self):
        return []

    async def get_trade_history(self, limit: int = 100):
        return []


class _FastProvider(_RetryProvider):
    provider_name = "fast"

    async def place_order(self, request: OrderRequest):
        return OrderResult(
            order_id="fast-1",
            symbol=request.symbol,
            side=request.side,
            volume=request.volume,
            fill_price=1.1,
            commission=0.0,
            success=True,
            submit_status="ACKED",
            fill_status="FILLED",
            broker_order_id="fast-1",
            raw_response={"provider": "fast"},
            raw_response_hash="h1",
        )


class _SlowProvider(_RetryProvider):
    provider_name = "slow"

    async def place_order(self, request: OrderRequest):
        return OrderResult(
            order_id="slow-1",
            symbol=request.symbol,
            side=request.side,
            volume=request.volume,
            fill_price=1.1,
            commission=0.0,
            success=True,
            submit_status="ACKED",
            fill_status="FILLED",
            broker_order_id="slow-1",
            raw_response={"provider": "slow"},
            raw_response_hash="h2",
        )


def _paper_command(*, candidates: list[str] | None = None) -> ExecutionCommand:
    ctx = PreExecutionContext(
        bot_instance_id="bot-1",
        runtime_mode="paper",
        provider_mode="paper",
        broker_connected=True,
        market_data_ok=True,
        data_age_seconds=0.0,
        spread_pips=0.0,
        confidence=1.0,
        rr=2.0,
        open_positions=0,
        daily_profit_amount=0.0,
        daily_loss_pct=0.0,
        consecutive_losses=0,
        daily_locked=False,
        kill_switch=False,
        idempotency_key="idem-smart-1",
        brain_cycle_id="cycle-1",
    )
    return ExecutionCommand(
        request=OrderRequest(
            symbol="EURUSD",
            side="buy",
            volume=0.01,
            order_type="market",
            idempotency_key="idem-smart-1",
            client_order_id="idem-smart-1",
        ),
        intent={"provider_candidates": candidates or []},
        pre_execution_context=ctx,
        idempotency_key="idem-smart-1",
        brain_cycle_id="cycle-1",
    )


@pytest.mark.asyncio
async def test_smart_policy_retries_transient_submit_error() -> None:
    provider = _RetryProvider()
    engine = ExecutionEngine(
        provider=provider,
        provider_name="default",
        runtime_mode="paper",
        gate_policy={
            "smart_execution_enabled": True,
            "smart_execution_max_retries": 1,
        },
    )
    engine._router.register("default", provider)

    result = await engine.place_order(_paper_command())

    assert result.success is True
    assert provider.calls == 2
    assert int((result.raw_response or {}).get("smart_execution_attempt", 0)) == 2


@pytest.mark.asyncio
async def test_smart_policy_routes_by_latency() -> None:
    primary = _RetryProvider()
    fast = _FastProvider()
    slow = _SlowProvider()

    engine = ExecutionEngine(
        provider=primary,
        provider_name="default",
        runtime_mode="paper",
        gate_policy={
            "smart_execution_enabled": True,
            "smart_execution_latency_aware": True,
            "smart_execution_max_retries": 0,
        },
    )
    engine._router.register("default", primary)
    engine._router.register("fast", fast)
    engine._router.register("slow", slow)

    # Seed historical latency so smart route prefers fast provider.
    engine._record_provider_latency("slow", 120.0)
    engine._record_provider_latency("fast", 12.0)

    result = await engine.place_order(_paper_command(candidates=["slow", "fast"]))

    assert result.success is True
    assert str((result.raw_response or {}).get("provider_name") or "") == "fast"
