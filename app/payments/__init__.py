"""Реестр платёжных провайдеров.

Провайдер собирается из настроек в БД (группа payment.*), которые полностью
редактируются в админ-панели без перезапуска кода (ТЗ п.35).
При изменении любой из ключевых настроек экземпляр пересоздаётся автоматически.
"""
from __future__ import annotations

import asyncio
from typing import Any

import structlog

from app.core.config import settings
from app.payments.base import (
    EXPORT_STATUS_CODES,
    METHODS,
    CreatePaymentRequest,
    CreatePaymentResult,
    ExportRequest,
    ExportResult,
    PaymentMethodInfo,
    PaymentProvider,
    PaymentProviderError,
    PaymentStatusResult,
    ProviderStatus,
    WebhookResult,
    normalize_status,
)
from app.payments.platega import PlategaProvider
from app.payments.stub import StubProvider

log = structlog.get_logger(__name__)

__all__ = [
    "METHODS",
    "EXPORT_STATUS_CODES",
    "PlategaProvider",
    "StubProvider",
    "ProviderStatus",
    "CreatePaymentRequest",
    "CreatePaymentResult",
    "PaymentStatusResult",
    "ExportRequest",
    "ExportResult",
    "WebhookResult",
    "PaymentProvider",
    "PaymentProviderError",
    "PaymentMethodInfo",
    "normalize_status",
    "ProviderRegistry",
    "registry",
    "get_provider",
    "reset_provider",
    "method_label",
]


async def _load_config(db: Any) -> dict[str, Any]:
    """Собрать настройки платежей из БД (с фоллбэком на .env)."""
    from app.services import cms  # ленивый импорт: избегаем цикла

    if db is None:
        return {
            "provider": settings.payment_provider,
            "enabled": True,
            "test_mode": settings.payment_provider != "platega",
            "methods": list(settings.payment_methods),
            "ttl_seconds": settings.payment_ttl_seconds,
            "send_metadata": True,
            "merchant_id": settings.platega_merchant_id or "",
            "secret": settings.platega_secret or "",
            "webhook_secret": settings.platega_webhook_secret or "",
            "webhook_require_signature": False,
            "base_url": settings.public_base_url,
        }

    group = await cms.get_settings_group(db, "payment")

    def value(key: str, default: Any = None) -> Any:
        raw = group.get(key, default)
        return default if raw in (None, "") else raw

    methods = value("methods", list(settings.payment_methods)) or []
    if isinstance(methods, str):
        methods = [m.strip() for m in methods.split(",") if m.strip()]
    return {
        "provider": str(value("provider", settings.payment_provider)),
        "enabled": bool(value("enabled", True)),
        "test_mode": bool(value("test_mode", False)),
        "methods": [int(m) for m in methods],
        "ttl_seconds": int(value("ttl_seconds", settings.payment_ttl_seconds)),
        "send_metadata": bool(value("send_metadata", True)),
        "merchant_id": str(value("merchant_id", settings.platega_merchant_id or "")),
        "secret": str(value("secret", settings.platega_secret or "")),
        "webhook_secret": str(value("webhook_secret", settings.platega_webhook_secret or "")),
        "webhook_require_signature": bool(value("webhook_require_signature", False)),
        "base_url": await cms.setting(db, "shop.domain", settings.public_base_url)
        or settings.public_base_url,
    }


def _build(config: dict[str, Any]) -> PaymentProvider:
    provider_name = (config.get("provider") or "stub").lower()
    if provider_name == "platega":
        return PlategaProvider(
            merchant_id=config["merchant_id"],
            secret=config["secret"],
            base_url=settings.platega_base_url,
            enabled=config["enabled"],
            test_mode=config["test_mode"],
            methods=config["methods"],
            ttl_seconds=config["ttl_seconds"],
            send_metadata=config["send_metadata"],
            webhook_secret=config["webhook_secret"],
            webhook_require_signature=config["webhook_require_signature"],
        )
    return StubProvider(
        base_url=str(config.get("base_url") or settings.public_base_url),
        auto_confirm=False,
        ttl_seconds=config["ttl_seconds"],
        methods=config["methods"],
    )


def _signature(config: dict[str, Any]) -> tuple:
    return (
        config.get("provider"),
        config.get("enabled"),
        config.get("test_mode"),
        tuple(config.get("methods") or ()),
        config.get("ttl_seconds"),
        config.get("send_metadata"),
        config.get("merchant_id"),
        config.get("secret"),
        config.get("webhook_secret"),
        config.get("webhook_require_signature"),
        config.get("base_url"),
    )


class ProviderRegistry:
    """Кэширует один экземпляр провайдера на процесс."""

    def __init__(self) -> None:
        self._provider: PaymentProvider | None = None
        self._signature: tuple | None = None
        self._lock = asyncio.Lock()

    async def get(self, db: Any = None) -> PaymentProvider:
        config = await _load_config(db)
        signature = _signature(config)
        if self._provider is not None and signature == self._signature:
            return self._provider
        async with self._lock:
            if self._provider is not None and signature == self._signature:
                return self._provider
            old = self._provider
            provider = _build(config)
            self._provider = provider
            self._signature = signature
            log.info(
                "payments.provider_ready",
                provider=provider.name,
                test_mode=config.get("test_mode"),
                methods=config.get("methods"),
            )
            if old is not None:
                try:
                    await old.aclose()
                except Exception as exc:  # pragma: no cover - защитная ветка
                    log.warning("payments.provider_close_failed", error=str(exc))
            return provider

    async def reset(self) -> None:
        async with self._lock:
            provider, self._provider, self._signature = self._provider, None, None
        if provider is not None:
            try:
                await provider.aclose()
            except Exception as exc:  # pragma: no cover
                log.warning("payments.provider_close_failed", error=str(exc))


registry = ProviderRegistry()


async def get_provider(db: Any = None) -> PaymentProvider:
    return await registry.get(db)


async def reset_provider() -> None:
    await registry.reset()


async def method_label(db: Any, code: int, locale: str = "ru") -> str:
    """Надпись метода оплаты: сначала текст из CMS, иначе встроенный."""
    info = METHODS.get(int(code))
    if info is None:
        return f"#{code}"
    if db is None:
        return info.label(locale)
    from app.services import cms

    text = await cms.t(db, info.text_key, locale)
    if text and not text.startswith("["):
        return text
    return info.label(locale)
