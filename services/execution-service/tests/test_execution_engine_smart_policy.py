from __future__ import annotations

import pytest

from execution_service.execution_engine import ExecutionEngine
from execution_service.providers.base import ExecutionCommand, OrderRequest, OrderResult, PreExecutionContext
from trading_core.runtime.pre_execution_gate import hash_gate_context, build_frozen_context_id, sign_gate_context


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


class _LivePrimaryProvider:
    mode = "live"
    is_connected = True
    provider_name = "primary"
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
        raise RuntimeError("primary_provider_down")

    async def close_position(self, position_id: str):
        return None

    async def get_open_positions(self):
        return []

    async def get_trade_history(self, limit: int = 100):
        return []


class _LiveBackupProvider(_LivePrimaryProvider):
    provider_name = "backup"

    async def place_order(self, request: OrderRequest):
        return OrderResult(
            order_id="backup-ok",
            symbol=request.symbol,
            side=request.side,
            volume=request.volume,
            fill_price=1.101,
            commission=0.0,
            success=True,
            submit_status="ACKED",
            fill_status="FILLED",
            broker_order_id="backup-ok",
            account_id="acc-live-1",
            raw_response_hash="h-live-1",
            raw_response={"provider": "backup"},
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


def _live_command(*, approval_id: int | None) -> ExecutionCommand:
    gate_context: dict = {
        "schema_version": "gate_context_v2",
        "provider_mode": "live",
        "runtime_mode": "live",
        "broker_connected": True,
        "market_data_ok": True,
        "data_age_seconds": 0.1,
        "spread_pips": 0.2,
        "confidence": 0.9,
        "rr": 2.0,
        "open_positions": 0,
        "daily_profit_amount": 0.0,
        "daily_loss_pct": 0.0,
        "consecutive_losses": 0,
        "daily_locked": False,
        "kill_switch": False,
        "idempotency_exists": False,
        "requested_volume": 0.01,
        "approved_volume": 0.01,
        "symbol": "EURUSD",
        "side": "buy",
        "account_id": "acc-live-1",
        "broker_name": "primary",
        "policy_version": "v1",
        "policy_version_id": "v1",
        "policy_status": "active",
        "policy_hash": "policy_hash_1",
        "quote_id": "q-1",
        "quote_timestamp": 1.0,
        "broker_server_time": 2.0,
        "instrument_spec_hash": "spec_hash_1",
        "broker_snapshot_hash": "broker_snapshot_hash_1",
        "broker_account_snapshot_hash": "broker_account_snapshot_hash_1",
        "risk_context_hash": "risk_context_hash_1",
        "idempotency_key": "idem-live-failover-1",
        "stop_loss": 1.09,
        "context_signature": "",
        "frozen_context_id": "",
    }
    frozen_context_id = build_frozen_context_id(gate_context)
    context_signature = sign_gate_context(gate_context, secret="test_secret_for_unit_tests") or "test_sig"
    gate_context["frozen_context_id"] = frozen_context_id
    gate_context["context_signature"] = context_signature

    ctx = PreExecutionContext(
        bot_instance_id="bot-live-1",
        runtime_mode="live",
        provider_mode="live",
        broker_connected=True,
        market_data_ok=True,
        data_age_seconds=0.1,
        spread_pips=0.2,
        confidence=0.9,
        rr=2.0,
        open_positions=0,
        daily_profit_amount=0.0,
        daily_loss_pct=0.0,
        consecutive_losses=0,
        daily_locked=False,
        kill_switch=False,
        idempotency_key="idem-live-failover-1",
        brain_cycle_id="cycle-live-1",
        account_id="acc-live-1",
        broker_name="primary",
        order_type="market",
        entry_price=1.1000,
        stop_loss=1.0900,
        take_profit=1.1200,
        policy_version="v1",
        gate_context=gate_context,
        context_hash=hash_gate_context(gate_context),
        frozen_context_id=frozen_context_id,
        context_signature=context_signature,
    )
    intent: dict = {
        "signal_id": "sig-1",
        "provider_candidates": ["backup"],
    }
    if approval_id is not None:
        intent["live_failover_approval"] = {
            "approval_id": approval_id,
            "actor_user_id": "user-1",
        }
    return ExecutionCommand(
        request=OrderRequest(
            symbol="EURUSD",
            side="buy",
            volume=0.01,
            order_type="market",
            price=1.1000,
            client_order_id="idem-live-failover-1",
            idempotency_key="idem-live-failover-1",
        ),
        intent=intent,
        pre_execution_context=ctx,
        idempotency_key="idem-live-failover-1",
        brain_cycle_id="cycle-live-1",
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


@pytest.mark.asyncio
async def test_live_failover_requires_approval_before_using_backup_provider() -> None:
    primary = _LivePrimaryProvider()
    backup = _LiveBackupProvider()

    async def _verify(*args, **kwargs):
        return True

    approvals: list[dict] = []

    async def _approval_hook(bot_instance_id: str, payload: dict) -> bool:
        approvals.append({"bot_instance_id": bot_instance_id, **dict(payload)})
        return False

    engine = ExecutionEngine(
        provider=primary,
        provider_name="primary",
        runtime_mode="live",
        verify_idempotency_reservation=_verify,
        on_live_failover_approval_hook=_approval_hook,
        gate_policy={
            "smart_execution_enabled": True,
            "smart_execution_live_failover_enabled": True,
            "smart_execution_live_failover_requires_approval": True,
            "smart_execution_retry_live": False,
        },
    )
    engine._router.register("primary", primary)
    engine._router.register("backup", backup)

    result = await engine.place_order(_live_command(approval_id=123))

    assert result.success is False
    assert "critical_unknown_enqueue_failed" in str(result.error_message or "")
    assert str((result.raw_response or {}).get("provider_name") or "") == "primary"
    assert len(approvals) == 1
    assert approvals[0]["approval_id"] == 123
    assert approvals[0]["primary_provider"] == "primary"
    assert approvals[0]["backup_providers"] == ["backup"]
    assert int(approvals[0]["approval_ttl_seconds"]) == 300
    assert bool(approvals[0]["reason_digest"])
    assert len(str(approvals[0]["reason_digest"])) == 64


@pytest.mark.asyncio
async def test_live_failover_uses_backup_provider_after_approval() -> None:
    primary = _LivePrimaryProvider()
    backup = _LiveBackupProvider()

    async def _verify(*args, **kwargs):
        return True

    async def _approval_hook(bot_instance_id: str, payload: dict) -> bool:
        return bool(payload.get("approval_id"))

    engine = ExecutionEngine(
        provider=primary,
        provider_name="primary",
        runtime_mode="live",
        verify_idempotency_reservation=_verify,
        on_live_failover_approval_hook=_approval_hook,
        gate_policy={
            "smart_execution_enabled": True,
            "smart_execution_live_failover_enabled": True,
            "smart_execution_live_failover_requires_approval": True,
            "smart_execution_retry_live": False,
        },
    )
    engine._router.register("primary", primary)
    engine._router.register("backup", backup)

    result = await engine.place_order(_live_command(approval_id=456))

    assert result.success is True
    assert str((result.raw_response or {}).get("provider_name") or "") == "backup"
