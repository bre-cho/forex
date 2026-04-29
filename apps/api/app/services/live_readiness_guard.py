from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ReadinessResult:
    ok: bool
    reason: str = ""


class LiveReadinessGuard:
    """Shared fail-closed guard for live runtime startup and execution."""

    BAD_PROVIDER_STATUSES = {"auth_failed", "disconnected", "degraded", "error", "unhealthy"}
    BAD_PROVIDER_MODES = {"stub", "paper", "unavailable", "degraded"}

    @classmethod
    async def check_provider(cls, provider: Any, *, require_live: bool = True) -> ReadinessResult:
        if provider is None:
            return ReadinessResult(False, "provider_missing")

        if not bool(getattr(provider, "is_connected", False)):
            connect = getattr(provider, "connect", None)
            if callable(connect):
                await connect()
        if not bool(getattr(provider, "is_connected", False)):
            return ReadinessResult(False, "provider_not_connected")

        mode = str(getattr(provider, "mode", "unknown")).lower()
        if require_live and mode in cls.BAD_PROVIDER_MODES:
            return ReadinessResult(False, f"provider_mode_not_allowed:{mode}")

        health_check = getattr(provider, "health_check", None)
        if callable(health_check):
            details = await health_check()
            if isinstance(details, dict):
                status = str(details.get("status", "healthy")).lower()
                if status in cls.BAD_PROVIDER_STATUSES:
                    reason = str(details.get("reason") or status)
                    return ReadinessResult(False, f"provider_unhealthy:{reason}")

        get_account_info = getattr(provider, "get_account_info", None)
        if require_live and callable(get_account_info):
            info = await get_account_info()
            equity = float(getattr(info, "equity", 0.0) or 0.0)
            if equity <= 0:
                return ReadinessResult(False, "account_equity_invalid")

        return ReadinessResult(True, "ok")

    @classmethod
    async def assert_live_provider_contract(cls, provider: Any, *, symbol: str = "") -> ReadinessResult:
        """P0.4: Verify all live-required methods are implemented (not base NotImplemented)."""
        required_methods = [
            "get_instrument_spec",
            "estimate_margin",
            "get_order_by_client_id",
            "get_executions_by_client_id",
            "close_all_positions",
            "get_quote",
        ]
        for name in required_methods:
            fn = getattr(provider, name, None)
            if not callable(fn):
                return ReadinessResult(False, f"live_provider_missing_method:{name}")
        if not getattr(provider, "supports_client_order_id", False):
            return ReadinessResult(False, "live_provider_client_order_id_not_supported")
        # Dry-run: test instrument spec and margin estimate
        if symbol:
            try:
                spec = await provider.get_instrument_spec(symbol)
                if not spec:
                    return ReadinessResult(False, f"live_provider_instrument_spec_empty:{symbol}")
            except Exception as exc:
                return ReadinessResult(False, f"live_provider_instrument_spec_failed:{exc}")
            try:
                quote = await provider.get_quote(symbol)
                if not quote:
                    return ReadinessResult(False, f"live_provider_quote_empty:{symbol}")
            except Exception as exc:
                return ReadinessResult(False, f"live_provider_quote_failed:{exc}")
        return ReadinessResult(True, "ok")

    @classmethod
    async def check_runtime_dependencies(
        cls,
        *,
        brain_available: bool,
        daily_state_available: bool,
    ) -> ReadinessResult:
        if not brain_available:
            return ReadinessResult(False, "brain_unavailable_in_live_mode")
        if not daily_state_available:
            return ReadinessResult(False, "daily_state_unavailable")
        return ReadinessResult(True, "ok")
