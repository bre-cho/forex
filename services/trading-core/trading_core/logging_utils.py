"""trading_core.logging_utils — Structured JSON logging helpers.

Provides a lightweight :class:`StructuredLogger` that wraps the standard
Python :class:`logging.Logger` and emits records as JSON-serialisable dicts
with a consistent set of context fields:

    - ``bot_instance_id``
    - ``cycle_id`` / ``brain_cycle_id``
    - ``engine``
    - ``action``
    - ``latency_ms``

Usage::

    from trading_core.logging_utils import get_structured_logger

    log = get_structured_logger("decision_engine", bot_instance_id="bot-123")
    log.info("gate_allowed", action="SCAN_AND_ENTER", latency_ms=4.2)
    log.warning("spread_too_wide", spread_pips=3.5)
    log.error("order_rejected", reason="insufficient_margin")

JSON log format
---------------
When the application uses :func:`configure_json_logging`, the root logger
emits records in JSON.  Otherwise the structured dict is appended to the
log message so it is visible in plain-text logs too.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional


def configure_json_logging(level: int = logging.INFO) -> None:
    """Install a JSON formatter on the root logger.

    Call once at application startup (e.g. in ``main.py`` or FastAPI
    ``lifespan``).  Subsequent ``logging.getLogger()`` calls will inherit
    the formatter.

    The emitted line format is::

        {"ts": 1234567890.123, "level": "INFO", "logger": "...",
         "msg": "...", "bot_instance_id": "...", ...extra fields...}
    """
    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        handler = logging.StreamHandler()
        root.addHandler(handler)
    for handler in root.handlers:
        if not isinstance(handler.formatter, _JsonFormatter):
            handler.setFormatter(_JsonFormatter())


class _JsonFormatter(logging.Formatter):
    """Log formatter that serialises the record as a JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": record.created,
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Merge any extra structured fields attached by StructuredLogger
        extra = getattr(record, "_structured", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        try:
            return json.dumps(payload, ensure_ascii=False, default=str)
        except Exception:
            return json.dumps({"msg": str(payload)})


class StructuredLogger:
    """Thin wrapper around :class:`logging.Logger` that injects structured
    context fields into every log record.

    Parameters
    ----------
    name:
        Logger name (typically the engine class name).
    bot_instance_id:
        ID of the bot this logger is scoped to.  Injected automatically into
        every record.
    extra:
        Additional static fields added to every record.
    """

    def __init__(
        self,
        name: str,
        *,
        bot_instance_id: str = "",
        **extra: Any,
    ) -> None:
        self._logger = logging.getLogger(name)
        self._base_ctx: dict[str, Any] = {
            k: v for k, v in extra.items() if v is not None
        }
        if bot_instance_id:
            self._base_ctx["bot_instance_id"] = bot_instance_id

    # ── public logging methods ─────────────────────────────────────────── #

    def debug(self, event: str, **fields: Any) -> None:
        self._emit(logging.DEBUG, event, fields)

    def info(self, event: str, **fields: Any) -> None:
        self._emit(logging.INFO, event, fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._emit(logging.WARNING, event, fields)

    def error(self, event: str, **fields: Any) -> None:
        self._emit(logging.ERROR, event, fields)

    def critical(self, event: str, **fields: Any) -> None:
        self._emit(logging.CRITICAL, event, fields)

    # ── context helpers ────────────────────────────────────────────────── #

    def bind(self, **fields: Any) -> "StructuredLogger":
        """Return a child logger with additional bound context fields."""
        child = StructuredLogger(self._logger.name)
        child._logger = self._logger
        child._base_ctx = {**self._base_ctx, **fields}
        return child

    # ── internals ──────────────────────────────────────────────────────── #

    def _emit(self, level: int, event: str, fields: dict[str, Any]) -> None:
        if not self._logger.isEnabledFor(level):
            return
        structured: dict[str, Any] = {**self._base_ctx, **fields}
        # Build a human-readable fallback message for plain-text log sinks.
        parts = [event]
        for k, v in structured.items():
            if k != "bot_instance_id":
                parts.append(f"{k}={v!r}")
        msg = " ".join(parts)
        record = self._logger.makeRecord(
            self._logger.name, level, "(unknown)", 0, msg, (), None
        )
        record._structured = {"event": event, **structured}  # type: ignore[attr-defined]
        self._logger.handle(record)


def get_structured_logger(
    name: str,
    *,
    bot_instance_id: str = "",
    **extra: Any,
) -> StructuredLogger:
    """Convenience factory that returns a :class:`StructuredLogger`.

    Parameters
    ----------
    name:
        Logger name.  Use ``__name__`` of the calling module, or a
        descriptive engine/service name.
    bot_instance_id:
        Bot instance context.
    extra:
        Any additional static key/value pairs to bind to all records.
    """
    return StructuredLogger(name, bot_instance_id=bot_instance_id, **extra)
