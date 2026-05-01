"""Tests for SignalDLQConsumer."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest

from signal_service.dlq_consumer import SignalDLQConsumer, DLQStats


# ── helpers ───────────────────────────────────────────────────────────────── #

def _make_redis_message(data: Any) -> dict:
    raw = json.dumps(data) if isinstance(data, dict) else data
    return {"type": "message", "data": raw.encode("utf-8") if isinstance(raw, str) else raw}


class _FakePubSub:
    def __init__(self, messages: list) -> None:
        self._messages = iter(messages)
        self.closed = False
        self.unsubscribed = False

    async def subscribe(self, *channels) -> None:
        pass

    async def get_message(self, *, ignore_subscribe_messages=True, timeout=1.0):
        try:
            return next(self._messages)
        except StopIteration:
            return None

    async def unsubscribe(self) -> None:
        self.unsubscribed = True

    async def close(self) -> None:
        self.closed = True


class _FakeRedis:
    def __init__(self, messages: list) -> None:
        self._messages = messages
        self._pubsub = _FakePubSub(messages)

    def pubsub(self) -> _FakePubSub:
        return self._pubsub


# ── tests ─────────────────────────────────────────────────────────────────── #

class TestDLQStats:
    def test_initial_state(self):
        stats = DLQStats()
        assert stats.total_received == 0
        assert stats.by_bot == {}

    def test_record_increments(self):
        stats = DLQStats()
        stats.record("bot-1")
        stats.record("bot-1")
        stats.record("bot-2")
        assert stats.total_received == 3
        assert stats.by_bot["bot-1"] == 2
        assert stats.by_bot["bot-2"] == 1

    def test_to_dict(self):
        stats = DLQStats()
        stats.record("bot-99")
        d = stats.to_dict()
        assert d["total_received"] == 1
        assert d["by_bot"]["bot-99"] == 1
        assert "last_entry_ts" in d


class TestSignalDLQConsumer:
    @pytest.mark.asyncio
    async def test_handle_entry_calls_callback(self):
        received: List[Dict] = []

        async def handler(entry: dict) -> None:
            received.append(entry)

        entry = {"bot_instance_id": "bot-42", "error": "boom", "original_channel": "signals:bot-42", "payload": "{}"}
        messages = [_make_redis_message(entry), None]
        redis = _FakeRedis(messages)
        consumer = SignalDLQConsumer(redis_client=redis, on_dlq_entry=handler)

        await consumer._handle_entry(json.dumps(entry))
        assert len(received) == 1
        assert received[0]["bot_instance_id"] == "bot-42"

    @pytest.mark.asyncio
    async def test_stats_incremented_on_entry(self):
        entry = {"bot_instance_id": "bot-10", "error": "x", "original_channel": "c", "payload": "{}"}
        redis = _FakeRedis([])
        consumer = SignalDLQConsumer(redis_client=redis)
        await consumer._handle_entry(json.dumps(entry))
        assert consumer.stats.total_received == 1
        assert consumer.stats.by_bot["bot-10"] == 1

    @pytest.mark.asyncio
    async def test_invalid_json_does_not_crash(self):
        redis = _FakeRedis([])
        consumer = SignalDLQConsumer(redis_client=redis)
        # Should not raise
        await consumer._handle_entry("NOT JSON {{{")
        assert consumer.stats.total_received == 0

    @pytest.mark.asyncio
    async def test_callback_exception_is_caught(self):
        async def bad_handler(entry: dict) -> None:
            raise ValueError("callback exploded")

        entry = {"bot_instance_id": "bot-1", "error": "e", "original_channel": "c", "payload": "{}"}
        redis = _FakeRedis([])
        consumer = SignalDLQConsumer(redis_client=redis, on_dlq_entry=bad_handler)
        # Should not propagate the exception
        await consumer._handle_entry(json.dumps(entry))
        assert consumer.stats.total_received == 1

    @pytest.mark.asyncio
    async def test_start_stop_lifecycle(self):
        redis = _FakeRedis([None])
        consumer = SignalDLQConsumer(redis_client=redis)
        await consumer.start()
        assert consumer.is_running
        await consumer.stop()
        assert not consumer.is_running

    @pytest.mark.asyncio
    async def test_double_start_is_idempotent(self):
        redis = _FakeRedis([None])
        consumer = SignalDLQConsumer(redis_client=redis)
        await consumer.start()
        task_first = consumer._task
        await consumer.start()  # second call should be no-op
        assert consumer._task is task_first
        await consumer.stop()
