"""Abstract base class for broker providers."""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd


@dataclass
class OrderRequest:
    symbol: str
    side: str          # 'buy' | 'sell'
    volume: float
    order_type: str    # 'market' | 'limit' | 'stop'
    price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    comment: str = ""
    client_order_id: str = ""
    idempotency_key: str = ""


@dataclass
class OrderResult:
    order_id: str
    symbol: str
    side: str
    volume: float
    fill_price: float
    commission: float
    success: bool
    client_order_id: Optional[str] = None
    broker_order_id: Optional[str] = None
    error_message: Optional[str] = None
    submit_status: str = "UNKNOWN"   # ACKED | REJECTED | UNKNOWN
    fill_status: str = "UNKNOWN"     # FILLED | PARTIAL | PENDING | UNKNOWN
    broker_position_id: Optional[str] = None
    broker_deal_id: Optional[str] = None
    account_id: Optional[str] = None
    server_time: Optional[float] = None
    latency_ms: float = 0.0
    raw_response_hash: Optional[str] = None
    raw_response: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionReceipt:
    idempotency_key: str
    broker_order_id: Optional[str]
    broker_position_id: Optional[str]
    broker_deal_id: Optional[str]
    submit_status: str
    fill_status: str
    requested_volume: float
    filled_volume: float
    avg_fill_price: Optional[float]
    commission: float
    raw_response: Dict[str, Any]
    latency_ms: float


@dataclass
class AccountInfo:
    balance: float
    equity: float
    margin: float
    free_margin: float
    margin_level: float
    currency: str
    account_id: Optional[str] = None
    broker_name: Optional[str] = None


@dataclass
class BrokerCapabilityProof:
    """Result of a live capability verification run.  All required fields must be True
    before live trading is permitted.  Populated by provider.verify_live_capability()."""

    provider: str
    mode: str
    account_authorized: bool = False
    account_id_match: bool = False
    quote_realtime: bool = False
    server_time_valid: bool = False
    instrument_spec_valid: bool = False
    margin_estimate_valid: bool = False
    client_order_id_supported: bool = False
    order_lookup_supported: bool = False
    execution_lookup_supported: bool = False
    close_all_supported: bool = False
    proof_timestamp: float = 0.0
    detail: Dict[str, Any] = field(default_factory=dict)

    @property
    def all_required_passed(self) -> bool:
        """All required live capability checks passed."""
        return all([
            self.account_authorized,
            self.account_id_match,
            self.quote_realtime,
            self.server_time_valid,
            self.instrument_spec_valid,
            self.client_order_id_supported,
            self.order_lookup_supported,
            self.close_all_supported,
        ])

    def failed_checks(self) -> List[str]:
        checks = {
            "account_authorized": self.account_authorized,
            "account_id_match": self.account_id_match,
            "quote_realtime": self.quote_realtime,
            "server_time_valid": self.server_time_valid,
            "instrument_spec_valid": self.instrument_spec_valid,
            "client_order_id_supported": self.client_order_id_supported,
            "order_lookup_supported": self.order_lookup_supported,
            "close_all_supported": self.close_all_supported,
        }
        return [k for k, v in checks.items() if not v]




@dataclass
class PreExecutionContext:
    bot_instance_id: str
    runtime_mode: str
    provider_mode: str
    broker_connected: bool
    market_data_ok: bool
    data_age_seconds: float
    spread_pips: float
    confidence: float
    rr: float
    open_positions: int
    daily_profit_amount: float
    daily_loss_pct: float
    consecutive_losses: int
    daily_locked: bool
    kill_switch: bool
    idempotency_key: str
    brain_cycle_id: str
    account_id: Optional[str] = None
    broker_name: str = ""
    order_type: str = "market"
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    policy_version: str = ""
    margin_usage_pct: float = 0.0
    free_margin_after_order: float = 0.0
    account_exposure_pct: float = 0.0
    symbol_exposure_pct: float = 0.0
    correlated_usd_exposure_pct: float = 0.0
    portfolio_daily_loss_pct: float = 0.0
    portfolio_open_positions: int = 0
    portfolio_kill_switch: bool = False
    policy_snapshot: Dict[str, Any] = field(default_factory=dict)
    gate_context: Dict[str, Any] = field(default_factory=dict)
    context_hash: str = ""


@dataclass
class ExecutionCommand:
    request: OrderRequest
    intent: Dict[str, Any]
    pre_execution_context: PreExecutionContext
    idempotency_key: str
    brain_cycle_id: str

class BrokerProvider(ABC):
    """Abstract broker provider — all concrete providers must implement this."""

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the broker."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection to the broker."""

    @abstractmethod
    async def get_account_info(self) -> AccountInfo:
        """Return current account information."""

    @abstractmethod
    async def get_candles(self, symbol: str, timeframe: str, limit: int = 200) -> pd.DataFrame:
        """Return OHLCV candle data."""

    @abstractmethod
    async def place_order(self, request: OrderRequest) -> OrderResult:
        """Place a market or pending order."""

    @abstractmethod
    async def close_position(self, position_id: str) -> OrderResult:
        """Close an open position by ID."""

    @abstractmethod
    async def get_open_positions(self) -> List[Dict[str, Any]]:
        """Return list of currently open positions."""

    @abstractmethod
    async def get_trade_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Return closed trade history."""

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Return True if the provider is currently connected."""

    # ------------------------------------------------------------------
    # Optional methods required for live mode (raise NotImplementedError
    # by default — live readiness guard checks these).
    # ------------------------------------------------------------------

    async def get_instrument_spec(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Return instrument/symbol specification dict.

        Live mode requires a real implementation from the provider.
        """
        raise NotImplementedError(f"get_instrument_spec not implemented for {type(self).__name__}")

    async def estimate_margin(self, symbol: str, side: str, volume: float, price: float) -> float:
        """Estimate required margin for an order in account currency.

        Live mode should use broker's own margin calculator if available.
        """
        raise NotImplementedError(f"estimate_margin not implemented for {type(self).__name__}")

    async def get_order_by_client_id(self, client_order_id: str) -> Optional[Dict[str, Any]]:
        """Look up an order by client/idempotency/comment id.

        Required for UnknownOrderReconciler in live mode.
        Returns None if not found.
        """
        raise NotImplementedError(f"get_order_by_client_id not implemented for {type(self).__name__}")

    async def get_executions_by_client_id(self, client_order_id: str) -> List[Dict[str, Any]]:
        """Return list of executions/deals for a given client order id.

        Required for UnknownOrderReconciler in live mode.
        Returns [] if not found.
        """
        raise NotImplementedError(f"get_executions_by_client_id not implemented for {type(self).__name__}")

    async def close_all_positions(self, symbol: Optional[str] = None) -> List[OrderResult]:
        """Close all open positions, optionally filtered by symbol."""
        raise NotImplementedError(f"close_all_positions not implemented for {type(self).__name__}")

    async def get_server_time(self) -> Optional[float]:
        """Return broker server UTC timestamp (epoch seconds). None if unsupported."""
        return None

    async def get_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Return current bid/ask quote for symbol. None if unsupported."""
        return None

    @property
    def supports_client_order_id(self) -> bool:
        """Return True if the provider can accept and look up orders by client order id.

        Execution service treats this as False-by-default; providers that implement
        get_order_by_client_id must override this property to return True.
        Live mode must fail if this returns False (no idempotency audit trail).
        """
        return False

    async def verify_live_capability(
        self,
        *,
        expected_account_id: Optional[str] = None,
        symbol: Optional[str] = None,
    ) -> "BrokerCapabilityProof":
        """Run all live capability checks and return a proof object.

        Default implementation performs a best-effort capability check using the
        methods available on this provider.  Live providers should override this
        to add provider-specific checks (e.g. clientMsgId roundtrip).

        Returns a BrokerCapabilityProof — caller must verify proof.all_required_passed.
        """
        proof = BrokerCapabilityProof(
            provider=type(self).__name__,
            mode=getattr(self, "mode", "unknown"),
            proof_timestamp=time.time(),
        )
        # 1. Account authorized
        try:
            info = await self.get_account_info()
            if info and float(getattr(info, "equity", 0.0) or 0.0) > 0:
                proof.account_authorized = True
                # 2. Account ID match
                provider_account_id = str(getattr(info, "account_id", "") or "")
                if expected_account_id:
                    proof.account_id_match = (provider_account_id == str(expected_account_id))
                else:
                    proof.account_id_match = bool(provider_account_id)
                proof.detail["account_id"] = provider_account_id
        except Exception as exc:
            proof.detail["account_error"] = str(exc)

        # 3. Server time valid
        try:
            srv_time = await self.get_server_time()
            if srv_time and abs(srv_time - time.time()) < 120:
                proof.server_time_valid = True
            proof.detail["server_time"] = srv_time
        except Exception as exc:
            proof.detail["server_time_error"] = str(exc)

        # 4. Quote realtime
        probe_symbol = symbol or "EURUSD"
        try:
            quote = await self.get_quote(probe_symbol)
            if quote and (quote.get("bid") or quote.get("ask")):
                proof.quote_realtime = True
            proof.detail["quote"] = quote
        except Exception as exc:
            proof.detail["quote_error"] = str(exc)

        # 5. Instrument spec valid
        try:
            spec = await self.get_instrument_spec(probe_symbol)
            if spec:
                proof.instrument_spec_valid = True
        except Exception as exc:
            proof.detail["instrument_spec_error"] = str(exc)

        # 6. Margin estimate valid
        try:
            margin = await self.estimate_margin(probe_symbol, "buy", 0.01, 1.1)
            if margin is not None and float(margin) >= 0:
                proof.margin_estimate_valid = True
        except Exception as exc:
            proof.detail["margin_estimate_error"] = str(exc)

        # 7. client_order_id supported
        proof.client_order_id_supported = bool(self.supports_client_order_id)

        # 8. Order lookup
        try:
            getattr(self, "get_order_by_client_id")
            proof.order_lookup_supported = True
        except AttributeError:
            pass

        # 9. Execution lookup
        try:
            getattr(self, "get_executions_by_client_id")
            proof.execution_lookup_supported = True
        except AttributeError:
            pass

        # 10. Close all
        try:
            getattr(self, "close_all_positions")
            proof.close_all_supported = True
        except AttributeError:
            pass

        return proof

