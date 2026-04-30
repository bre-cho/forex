from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from execution_service.providers.base import LiveBrokerProviderProtocol


@dataclass
class ReadinessResult:
    ok: bool
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)


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
    async def require_capability_proof(
        cls,
        provider: Any,
        *,
        expected_account_id: str | None = None,
        symbol: str | None = None,
        timeframe: str | None = None,
    ) -> ReadinessResult:
        """P0.1: Run BrokerCapabilityProof and fail-closed if any required check fails.

        Should be called during live start preflight.  Providers that do not implement
        verify_live_capability() will fail with provider_capability_proof_unavailable.
        """
        verify = getattr(provider, "verify_live_capability", None)
        if not callable(verify):
            return ReadinessResult(False, "provider_capability_proof_unavailable")
        try:
            try:
                proof = await verify(
                    expected_account_id=expected_account_id,
                    symbol=symbol,
                    timeframe=timeframe,
                )
            except TypeError:
                proof = await verify(
                    expected_account_id=expected_account_id,
                    symbol=symbol,
                )
        except Exception as exc:
            return ReadinessResult(False, f"provider_capability_proof_error:{exc}")
        if not proof.all_required_passed:
            failed = ",".join(proof.failed_checks())
            return ReadinessResult(
                False,
                f"broker_capability_proof_failed:{failed}",
                details={
                    "failed_checks": proof.failed_checks(),
                    "provider": str(getattr(proof, "provider", "")),
                    "mode": str(getattr(proof, "mode", "")),
                    "symbol": str(symbol or ""),
                    "timeframe": str(timeframe or ""),
                },
            )
        return ReadinessResult(
            True,
            "ok",
            details={
                "provider": str(getattr(proof, "provider", "")),
                "mode": str(getattr(proof, "mode", "")),
                "symbol": str(symbol or ""),
                "timeframe": str(timeframe or ""),
                "proof_timestamp": float(getattr(proof, "proof_timestamp", 0.0) or 0.0),
                "failed_checks": list(getattr(proof, "failed_checks")() if callable(getattr(proof, "failed_checks", None)) else []),
                "detail": dict(getattr(proof, "detail", {}) or {}),
            },
        )

    @classmethod
    async def assert_live_provider_contract(cls, provider: Any, *, symbol: str = "") -> ReadinessResult:
        """P0.4: Verify all live-required methods are implemented (not base NotImplemented)."""
        if not isinstance(provider, LiveBrokerProviderProtocol):
            return ReadinessResult(False, "live_provider_protocol_non_compliant")
        required_methods = [
            "get_instrument_spec",
            "estimate_margin",
            "get_order_by_client_id",
            "get_executions_by_client_id",
            "close_all_positions",
            "get_quote",
            "get_server_time",
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
                if not isinstance(spec, dict):
                    return ReadinessResult(False, f"live_provider_instrument_spec_empty:{symbol}")
                required_spec = ["pip_size", "contract_size", "min_volume", "volume_step"]
                missing = [k for k in required_spec if float(spec.get(k) or spec.get({"min_volume": "min_lot", "volume_step": "lot_step"}.get(k, "")) or 0.0) <= 0.0]
                if missing:
                    return ReadinessResult(False, f"live_provider_instrument_spec_invalid:{','.join(missing)}")
            except Exception as exc:
                return ReadinessResult(False, f"live_provider_instrument_spec_failed:{exc}")
            try:
                quote = await provider.get_quote(symbol)
                if not isinstance(quote, dict):
                    return ReadinessResult(False, f"live_provider_quote_empty:{symbol}")
                if float(quote.get("bid") or 0.0) <= 0 or float(quote.get("ask") or 0.0) <= 0:
                    return ReadinessResult(False, "live_provider_quote_invalid_bid_ask")
                if not str(quote.get("quote_id") or ""):
                    return ReadinessResult(False, "live_provider_quote_missing_quote_id")
                qts = float(quote.get("timestamp") or 0.0)
                if qts <= 0:
                    return ReadinessResult(False, "live_provider_quote_missing_timestamp")
                if abs(__import__("time").time() - qts) > 30.0:
                    return ReadinessResult(False, "live_provider_quote_stale")
            except Exception as exc:
                return ReadinessResult(False, f"live_provider_quote_failed:{exc}")
            try:
                margin = await provider.estimate_margin(symbol, "buy", 0.01, float(quote.get("ask") or 0.0))
                if float(margin or 0.0) <= 0.0:
                    return ReadinessResult(False, "live_provider_margin_invalid")
            except Exception as exc:
                return ReadinessResult(False, f"live_provider_margin_failed:{exc}")
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
