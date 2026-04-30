"""Canonical broker snapshot dataclasses with deterministic hash contracts.

BrokerQuoteSnapshot  — point-in-time bid/ask quote from broker
BrokerInstrumentSpecSnapshot — static instrument specification from broker

Both expose a `canonical_hash` property computed from broker-native fields only
(no derived fields, no Python object identity). This hash is the gate binding
used by PreExecutionGate and risk_context_builder to detect stale/mutated
context between signal generation and order submission.

Audit spec: P0-D — Broker Snapshot Hash Contract
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Iterable, Optional


def _stable_hash(data: dict) -> str:
    """SHA-256 of JSON-serialised dict with sorted keys, hex-encoded."""
    raw = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class BrokerQuoteSnapshot:
    """Point-in-time bid/ask quote captured from the broker feed.

    Args:
        symbol: Broker-normalised instrument symbol, e.g. "EURUSD".
        bid: Broker bid price (float, > 0).
        ask: Broker ask price (float, >= bid).
        timestamp: Unix epoch seconds (float) from broker feed.
        quote_id: Opaque broker-assigned quote identifier (non-empty).
        source: Provider name, e.g. "ctrader_live".
        latency_ms: Round-trip latency in milliseconds to obtain this quote.
    """

    symbol: str
    bid: float
    ask: float
    timestamp: float
    quote_id: str
    source: str
    latency_ms: float = 0.0
    captured_at: float = field(default_factory=time.time)

    @property
    def spread(self) -> float:
        return round(self.ask - self.bid, 10)

    @property
    def mid(self) -> float:
        return round((self.bid + self.ask) / 2.0, 10)

    @property
    def age_seconds(self) -> float:
        return time.time() - self.timestamp

    @property
    def canonical_hash(self) -> str:
        """Deterministic hash of broker-native fields only (no derived values)."""
        return _stable_hash(
            {
                "symbol": self.symbol,
                "bid": self.bid,
                "ask": self.ask,
                "timestamp": self.timestamp,
                "quote_id": self.quote_id,
                "source": self.source,
            }
        )

    def is_fresh(self, max_age_seconds: float = 30.0) -> bool:
        return self.age_seconds <= max_age_seconds

    def validate(self) -> None:
        """Raise ValueError if quote is internally inconsistent."""
        if not self.symbol:
            raise ValueError("BrokerQuoteSnapshot: symbol must be non-empty")
        if not self.quote_id:
            raise ValueError("BrokerQuoteSnapshot: quote_id must be non-empty")
        if self.bid <= 0:
            raise ValueError(f"BrokerQuoteSnapshot: bid must be > 0, got {self.bid}")
        if self.ask < self.bid:
            raise ValueError(
                f"BrokerQuoteSnapshot: ask ({self.ask}) must be >= bid ({self.bid})"
            )
        if self.timestamp <= 0:
            raise ValueError(f"BrokerQuoteSnapshot: timestamp must be > 0, got {self.timestamp}")

    def to_conversion_quote(self) -> dict:
        """Compact quote payload for currency conversion/risk services."""
        return {
            "bid": float(self.bid),
            "ask": float(self.ask),
            "mid": float(self.mid),
            "timestamp": float(self.timestamp),
            "quote_id": str(self.quote_id),
            "source": str(self.source),
        }


@dataclass(frozen=True)
class BrokerInstrumentSpecSnapshot:
    """Static instrument specification captured from the broker.

    All numeric sizing fields must be > 0. This snapshot is immutable
    (frozen dataclass) to prevent post-capture mutation.

    Args:
        symbol: Broker-normalised instrument symbol.
        pip_size: One pip in price units (e.g. 0.0001 for EURUSD).
        tick_size: Minimum price movement (often == pip_size).
        contract_size: Units per lot (e.g. 100_000 for standard forex lot).
        min_volume: Minimum tradeable volume in lots.
        max_volume: Maximum tradeable volume in lots.
        volume_step: Volume granularity (e.g. 0.01).
        margin_rate: Fraction of notional required as margin (e.g. 0.01 = 1:100).
        currency_profit: Settlement currency for P&L.
        currency_margin: Currency in which margin is held.
        source: Provider name.
        captured_at: Unix epoch seconds when spec was fetched.
    """

    symbol: str
    pip_size: float
    tick_size: float
    contract_size: float
    min_volume: float
    max_volume: float
    volume_step: float
    margin_rate: float
    currency_profit: str
    currency_margin: str
    source: str = ""
    captured_at: float = field(default_factory=time.time)

    @property
    def canonical_hash(self) -> str:
        """Deterministic hash of broker-native sizing fields only."""
        return _stable_hash(
            {
                "symbol": self.symbol,
                "pip_size": self.pip_size,
                "tick_size": self.tick_size,
                "contract_size": self.contract_size,
                "min_volume": self.min_volume,
                "max_volume": self.max_volume,
                "volume_step": self.volume_step,
                "margin_rate": self.margin_rate,
                "currency_profit": self.currency_profit,
                "currency_margin": self.currency_margin,
                "source": self.source,
            }
        )

    def validate(self) -> None:
        """Raise ValueError if spec contains any zero/negative sizing fields."""
        errors: list[str] = []
        for fname in ("pip_size", "tick_size", "contract_size", "min_volume", "volume_step"):
            val = getattr(self, fname)
            if val <= 0:
                errors.append(f"{fname}={val} must be > 0")
        if self.max_volume <= 0:
            errors.append(f"max_volume={self.max_volume} must be > 0")
        if self.margin_rate <= 0:
            errors.append(f"margin_rate={self.margin_rate} must be > 0")
        if not self.symbol:
            errors.append("symbol must be non-empty")
        if errors:
            raise ValueError(f"BrokerInstrumentSpecSnapshot: {'; '.join(errors)}")


def build_quote_snapshot_index(snapshots: Iterable[BrokerQuoteSnapshot]) -> dict[str, dict]:
    """Create symbol->quote dict compatible with CurrencyConversionService."""
    result: dict[str, dict] = {}
    for item in snapshots:
        if not isinstance(item, BrokerQuoteSnapshot):
            continue
        result[str(item.symbol).upper()] = item.to_conversion_quote()
    return result
