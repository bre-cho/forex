from __future__ import annotations

import os


def app_env() -> str:
    return str(os.getenv("APP_ENV", "local") or "local").strip().lower()


def allow_stub_runtime() -> bool:
    """Stub runtime policy: local/dev allowed, test requires explicit opt-in."""
    env = app_env()
    if env in {"local", "dev", "development"}:
        return True
    flag = str(os.getenv("ALLOW_STUB_RUNTIME", "false") or "false").strip().lower() == "true"
    return env == "test" and flag


def production_like_env() -> bool:
    return app_env() in {"staging", "production"}


def enforce_stub_runtime_allowed() -> None:
    """Fail closed when stub runtime fallback is not allowed."""
    if production_like_env():
        raise RuntimeError("stub_runtime_forbidden_in_staging_or_production")
    if not allow_stub_runtime():
        raise RuntimeError("stub_runtime_requires_local_dev_or_app_env_test_with_allow_stub_runtime_true")
