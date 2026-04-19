"""Credential encryption helpers for broker connection secrets."""
from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import get_settings


_REDACT_KEYS = {
    "password",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "client_secret",
}


def _build_derived_key(seed: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(seed.encode("utf-8")).digest()).decode("utf-8")


def _candidate_fernet_keys() -> list[str]:
    settings = get_settings()
    keys: list[str] = []
    if settings.fernet_key:
        keys.append(settings.fernet_key.strip())
    if settings.fernet_key_previous:
        keys.extend([k.strip() for k in settings.fernet_key_previous.split(",") if k.strip()])
    if not keys:
        secret = settings.secret_key or settings.jwt_secret or "dev-insecure-secret"
        keys.append(_build_derived_key(secret))
    return keys


def _fernet() -> MultiFernet:
    fernets = [Fernet(key.encode("utf-8")) for key in _candidate_fernet_keys()]
    return MultiFernet(fernets)


def encrypt_credentials(credentials: dict[str, Any]) -> str:
    payload = json.dumps(credentials or {}, separators=(",", ":"), sort_keys=True)
    return _fernet().encrypt(payload.encode("utf-8")).decode("utf-8")


def decrypt_credentials(credentials_encrypted: str | None) -> dict[str, Any]:
    if not credentials_encrypted:
        return {}
    try:
        plaintext = _fernet().decrypt(credentials_encrypted.encode("utf-8")).decode("utf-8")
        value = json.loads(plaintext)
        return value if isinstance(value, dict) else {}
    except (InvalidToken, ValueError, TypeError, json.JSONDecodeError):
        return {}


def redact_credentials(credentials: dict[str, Any] | None) -> dict[str, Any]:
    safe = credentials or {}
    redacted: dict[str, Any] = {}
    for key, value in safe.items():
        lowered = str(key).lower()
        if lowered in _REDACT_KEYS or any(token in lowered for token in _REDACT_KEYS):
            redacted[key] = "***REDACTED***"
        else:
            redacted[key] = value
    return redacted
