"""Credential encryption helpers for broker connection secrets."""
from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


def _fernet() -> Fernet:
    settings = get_settings()
    secret = settings.secret_key or settings.jwt_secret or "dev-insecure-secret"
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


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

