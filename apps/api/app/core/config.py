"""Application configuration via pydantic-settings."""
from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    app_env: str = "development"
    app_name: str = "Forex Trading Platform"
    app_url: str = "http://localhost:8000"
    frontend_url: str = "http://localhost:3000"
    secret_key: str = ""
    debug: bool = False
    log_level: str = "INFO"
    enable_reconciliation_daemon: bool = True
    enable_submit_outbox_recovery_worker: bool = True
    enable_legacy_routes: bool = False
    allow_stub_runtime: bool = False

    # Live safety gates
    live_cert_max_age_hours: int = 168
    live_burn_in_required: bool = True
    max_equity_drift_pct: float = 1.0
    require_signed_gate_context: bool = True
    unknown_gate_timeout_seconds: int = 60

    # JWT
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 30

    # Database
    database_url: str = "postgresql+asyncpg://forex:forex_dev@localhost:5432/forex_db"
    database_pool_size: int = 10
    database_max_overflow: int = 20

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    redis_cache_ttl: int = 300

    # CORS
    cors_origins: str = "http://localhost:3000,http://localhost:3001"

    # WebSocket hardening
    ws_idle_timeout_seconds: int = 90
    ws_max_connections_per_user: int = 10
    ws_max_connections_per_user_per_workspace: int = 5

    # Credential encryption
    fernet_key: str = ""
    fernet_key_previous: str = ""

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    # cTrader
    ctrader_client_id: str = ""
    ctrader_client_secret: str = ""
    ctrader_access_token: str = ""
    ctrader_refresh_token: str = ""
    ctrader_account_id: str = ""
    ctrader_symbol: str = "EURUSD"
    ctrader_timeframe: str = "M5"
    ctrader_live: bool = False

    # LLM
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-3-5-sonnet-20241022"
    llm_provider: str = "openai"

    # Email
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = "noreply@example.com"
    smtp_from_name: str = "Forex Platform"
    smtp_tls: bool = True

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Stripe
    stripe_secret_key: str = ""
    stripe_publishable_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_starter: str = ""
    stripe_price_pro: str = ""
    stripe_price_enterprise: str = ""

    # Monitoring
    sentry_dsn: str = ""
    prometheus_enabled: bool = True

    @property
    def is_production(self) -> bool:
        return str(self.app_env).lower() == "production"

    @field_validator("secret_key", "jwt_secret", mode="after")
    @classmethod
    def check_secrets_not_empty(cls, v: str, info) -> str:
        import os
        if os.getenv("APP_ENV") == "production":
            value = str(v or "").strip()
            lowered = value.lower()
            if not value:
                raise ValueError(
                    f"'{info.field_name}' must be set to a secure value in production"
                )
            if "change_me" in lowered:
                raise ValueError(
                    f"'{info.field_name}' must not use placeholder values in production"
                )
            if len(value) < 32:
                raise ValueError(
                    f"'{info.field_name}' must be at least 32 characters in production"
                )
        return v

    @model_validator(mode="after")
    def validate_production_safety(self) -> "Settings":
        if not self.is_production:
            return self

        db_url = str(self.database_url or "").strip().lower()
        if not db_url.startswith("postgresql"):
            raise ValueError("database_url must use PostgreSQL in production")
        if "sqlite" in db_url:
            raise ValueError("sqlite is not allowed in production")

        redis_url = str(self.redis_url or "").strip().lower()
        if not redis_url.startswith("redis://") and not redis_url.startswith("rediss://"):
            raise ValueError("redis_url must be configured in production")

        if bool(self.debug):
            raise ValueError("debug must be false in production")
        if bool(self.enable_legacy_routes):
            raise ValueError("enable_legacy_routes must be false in production")
        if bool(self.allow_stub_runtime):
            raise ValueError("allow_stub_runtime must be false in production")

        fernet = str(self.fernet_key or "").strip()
        if not fernet:
            raise ValueError("fernet_key must be set in production")

        placeholders = ("change_me", "placeholder", "example", "dummy", "test")
        secret_values = [
            ("secret_key", str(self.secret_key or "")),
            ("jwt_secret", str(self.jwt_secret or "")),
            ("fernet_key", fernet),
        ]
        for field_name, value in secret_values:
            lowered = value.lower()
            if any(token in lowered for token in placeholders):
                raise ValueError(f"{field_name} must not use placeholder values in production")

        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
