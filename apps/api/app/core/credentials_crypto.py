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


def _is_valid_fernet_key(key: str) -> bool:
    try:
        decoded = base64.urlsafe_b64decode(key.encode("utf-8"))
        return len(decoded) == 32
    except Exception:
        return False


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
    settings = get_settings()
    valid_keys = [key for key in _candidate_fernet_keys() if _is_valid_fernet_key(key)]
    if not valid_keys:
        if settings.app_env == "production":
            raise ValueError(
                "FERNET_KEY environment variable is required in production. "
                "Generate one with: python -c "
                "\"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
        valid_keys = [_build_derived_key("dev-insecure-secret")]
    fernets = [Fernet(key.encode("utf-8")) for key in valid_keys]
    return MultiFernet(fernets)


def _parse_envelope(value: str) -> tuple[str | None, str]:
    text = str(value or "").strip()
    if not text:
        return None, ""
    if ":" not in text:
        return None, text
    version, ciphertext = text.split(":", 1)
    if not version.strip() or not ciphertext.strip():
        return None, text
    return version.strip(), ciphertext.strip()


def encrypt_credentials(credentials: dict[str, Any]) -> str:
    settings = get_settings()
    version = str(getattr(settings, "fernet_key_version", "v1") or "v1").strip() or "v1"
    payload = json.dumps(credentials or {}, separators=(",", ":"), sort_keys=True)
    token = _fernet().encrypt(payload.encode("utf-8")).decode("utf-8")
    return f"{version}:{token}"


def decrypt_credentials(credentials_encrypted: str | None) -> dict[str, Any]:
    if not credentials_encrypted:
        return {}
    _version, ciphertext = _parse_envelope(str(credentials_encrypted or ""))
    if not ciphertext:
        return {}
    try:
        plaintext = _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        value = json.loads(plaintext)
        return value if isinstance(value, dict) else {}
    except (InvalidToken, ValueError, TypeError, json.JSONDecodeError):
        return {}


def rotate_credentials_encryption(credentials_encrypted: str | None) -> str:
    plaintext = decrypt_credentials(credentials_encrypted)
    return encrypt_credentials(plaintext)


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
