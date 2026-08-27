"""Централизованная конфигурация. Секреты только из окружения (.env)."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # ---------- APP ----------
    app_env: Literal["local", "production"] = "local"
    app_name: str = "Premium Shop"
    secret_key: str = "change-me"
    base_url: str = "http://localhost:8000"
    log_level: str = "INFO"
    default_locale: str = "ru"
    supported_locales: str = "ru,en"
    timezone: str = "Europe/Moscow"

    # ---------- TELEGRAM ----------
    bot_token: str = ""
    bot_mode: Literal["polling", "webhook"] = "polling"
    bot_webhook_path: str = "/telegram/webhook"
    bot_webhook_secret: str = ""
    admin_ids: str = ""
    required_channel_id: str = ""
    required_channel_url: str = ""
    support_username: str = ""
    user_agreement_url: str = ""
    privacy_policy_url: str = ""

    # ---------- DATABASE ----------
    database_url: str = "postgresql+asyncpg://shop:shop@localhost:5432/shop"
    db_pool_size: int = 20
    db_max_overflow: int = 10
    db_pool_timeout: int = 10
    db_echo: bool = False

    # ---------- REDIS ----------
    redis_url: str = "redis://localhost:6379/0"
    cache_ttl: int = 300

    # ---------- WEB ADMIN ----------
    admin_path: str = "/admin"
    admin_session_max_age: int = 43200
    admin_bootstrap_login: str = "admin"
    admin_bootstrap_password: str = ""

    # ---------- PAYMENTS ----------
    payment_provider: Literal["platega", "stub"] = "stub"
    platega_base_url: str = "https://app.platega.io"
    platega_merchant_id: str = ""
    platega_secret: str = ""
    platega_webhook_secret: str = ""
    platega_test_mode: bool = True
    platega_send_metadata: bool = True
    payment_ttl_seconds: int = 900
    payment_poll_interval: int = 5

    # ---------- derived ----------
    @field_validator("supported_locales")
    @classmethod
    def _strip_locales(cls, v: str) -> str:
        return ",".join(p.strip() for p in v.split(",") if p.strip())

    @property
    def locales(self) -> list[str]:
        return [p for p in self.supported_locales.split(",") if p]

    @property
    def admin_id_list(self) -> list[int]:
        out: list[int] = []
        for chunk in self.admin_ids.replace(";", ",").split(","):
            chunk = chunk.strip()
            if chunk.isdigit():
                out.append(int(chunk))
        return out

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def telegram_webhook_url(self) -> str:
        return f"{self.base_url.rstrip('/')}{self.bot_webhook_path}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
