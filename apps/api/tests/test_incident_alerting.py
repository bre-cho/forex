"""P1.2 Incident Alerting — unit tests for incident_notifier."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.incident_notifier import (
    _should_alert,
    _format_alert_text,
    notify_incident,
    _ALWAYS_ALERT_TYPES,
)


def test_should_alert_always_alert_types():
    for t in _ALWAYS_ALERT_TYPES:
        assert _should_alert(t, "warning") is True


def test_should_alert_critical():
    assert _should_alert("random_type", "critical") is True


def test_should_alert_error():
    assert _should_alert("random_type", "error") is True


def test_should_not_alert_warning_normal():
    assert _should_alert("random_type", "warning") is False


def test_should_not_alert_info():
    assert _should_alert("random_type", "info") is False


def test_format_alert_text_contains_type_and_severity():
    text = _format_alert_text("unknown_order_escalated", "critical", "Test title", "Test detail")
    assert "CRITICAL" in text
    assert "unknown_order_escalated" in text
    assert "Test title" in text
    assert "Test detail" in text


def test_format_alert_text_truncates_detail():
    long_detail = "x" * 500
    text = _format_alert_text("t", "critical", "title", long_detail)
    # Detail is truncated to 400 chars
    assert len(text) < 600


@pytest.mark.asyncio
async def test_notify_incident_no_channels_configured(monkeypatch):
    """Should not raise even with no env vars set."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("DISCORD_INCIDENT_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("INCIDENT_WEBHOOK_URL", raising=False)
    # Should complete without error
    await notify_incident(
        incident_type="unknown_order_escalated",
        severity="critical",
        title="Test",
        detail="test detail",
    )


@pytest.mark.asyncio
async def test_notify_incident_calls_telegram_for_critical(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123456")
    monkeypatch.delenv("DISCORD_INCIDENT_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("INCIDENT_WEBHOOK_URL", raising=False)

    calls = []

    async def fake_to_thread(fn, *args, **kwargs):
        calls.append(fn.__name__ if hasattr(fn, "__name__") else str(fn))

    with patch("asyncio.to_thread", fake_to_thread):
        await notify_incident(
            incident_type="unknown_order_escalated",
            severity="critical",
            title="Test",
            detail="detail",
        )
    assert len(calls) >= 1  # at least telegram _send was called


@pytest.mark.asyncio
async def test_notify_incident_low_severity_no_telegram(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123456")
    monkeypatch.delenv("INCIDENT_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("DISCORD_INCIDENT_WEBHOOK_URL", raising=False)

    calls = []

    async def fake_to_thread(fn, *args, **kwargs):
        calls.append("to_thread_called")

    with patch("asyncio.to_thread", fake_to_thread):
        await notify_incident(
            incident_type="low_severity_event",
            severity="info",
            title="Info",
            detail="detail",
        )
    # info + non-alertable type should NOT call telegram
    assert len(calls) == 0


@pytest.mark.asyncio
async def test_notify_incident_warning_for_always_alert_type(monkeypatch):
    """Always-alert types should trigger even with warning severity."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123456")
    monkeypatch.delenv("INCIDENT_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("DISCORD_INCIDENT_WEBHOOK_URL", raising=False)

    calls = []

    async def fake_to_thread(fn, *args, **kwargs):
        calls.append("called")

    with patch("asyncio.to_thread", fake_to_thread):
        await notify_incident(
            incident_type="kill_switch_enabled",
            severity="warning",  # warning but always-alert type
            title="Kill switch",
            detail="manual reset",
        )
    assert len(calls) >= 1
