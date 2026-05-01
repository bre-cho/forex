from __future__ import annotations

from app.core import credentials_crypto as cc


def test_encrypt_decrypt_roundtrip_with_version_prefix() -> None:
    payload = {
        "client_id": "abc",
        "client_secret": "secret",
        "access_token": "token",
    }
    encrypted = cc.encrypt_credentials(payload)
    assert ":" in encrypted
    assert cc.decrypt_credentials(encrypted) == payload


def test_decrypt_supports_legacy_ciphertext_without_prefix() -> None:
    payload = {"api_key": "k1", "refresh_token": "r1"}
    plaintext = cc._fernet().encrypt(cc.json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")).decode("utf-8")
    assert cc.decrypt_credentials(plaintext) == payload


def test_rotate_credentials_encryption_keeps_plaintext() -> None:
    payload = {"account_id": 123, "token": "abc"}
    encrypted = cc.encrypt_credentials(payload)
    rotated = cc.rotate_credentials_encryption(encrypted)
    assert cc.decrypt_credentials(rotated) == payload
    assert ":" in rotated
