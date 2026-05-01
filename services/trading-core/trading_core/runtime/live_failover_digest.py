from __future__ import annotations

import hashlib
import json
from typing import Any


def normalize_live_failover_reason_payload(
    *,
    bot_instance_id: str,
    idempotency_key: str,
    brain_cycle_id: str,
    signal_id: str | None,
    symbol: str,
    side: str,
    primary_provider: str,
    backup_providers: list[str],
) -> dict[str, Any]:
    _bot = str(bot_instance_id or "").strip()
    _idempotency = str(idempotency_key or "").strip()
    _cycle = str(brain_cycle_id or "").strip()
    _symbol = str(symbol or "").strip().upper()
    _side = str(side or "").strip().lower()
    _primary = str(primary_provider or "").strip().lower()
    _backups = sorted(
        str(item or "").strip().lower()
        for item in list(backup_providers or [])
        if str(item or "").strip()
    )
    _signal = str(signal_id or "").strip() or None

    if not _bot:
        raise ValueError("missing_bot_instance_id")
    if not _idempotency:
        raise ValueError("missing_idempotency_key")
    if not _cycle:
        raise ValueError("missing_brain_cycle_id")
    if not _symbol:
        raise ValueError("missing_symbol")
    if not _side:
        raise ValueError("missing_side")
    if not _primary:
        raise ValueError("missing_primary_provider")
    if not _backups:
        raise ValueError("missing_backup_providers")

    return {
        "bot_instance_id": _bot,
        "idempotency_key": _idempotency,
        "brain_cycle_id": _cycle,
        "signal_id": _signal,
        "symbol": _symbol,
        "side": _side,
        "primary_provider": _primary,
        "backup_providers": _backups,
    }


def build_live_failover_reason_digest(payload: dict[str, Any]) -> str:
    required = [
        "bot_instance_id",
        "idempotency_key",
        "brain_cycle_id",
        "symbol",
        "side",
        "primary_provider",
        "backup_providers",
    ]
    for key in required:
        value = payload.get(key)
        if key == "backup_providers":
            if not isinstance(value, list) or len(value) == 0:
                raise ValueError("missing_backup_providers")
            continue
        if str(value or "").strip() == "":
            raise ValueError(f"missing_{key}")

    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
