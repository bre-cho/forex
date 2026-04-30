from __future__ import annotations

from trading_core.risk.broker_snapshot import BrokerQuoteSnapshot, build_quote_snapshot_index


def test_build_quote_snapshot_index_uses_upper_symbol_key() -> None:
    q = BrokerQuoteSnapshot(
        symbol="eurusd",
        bid=1.10,
        ask=1.12,
        timestamp=1_700_000_000.0,
        quote_id="q-1",
        source="test",
    )
    index = build_quote_snapshot_index([q])
    assert "EURUSD" in index
    assert index["EURUSD"]["mid"] == 1.11
    assert index["EURUSD"]["quote_id"] == "q-1"
