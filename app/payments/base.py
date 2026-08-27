"""Абстракция платёжного провайдера (ТЗ п.20-п.23).

Любой провайдер (Platega, заглушка, будущие) реализует один и тот же
интерфейс, поэтому бизнес-логика (services/payments.py) не зависит
от конкретного API. Сеть — только асинхронная (httpx.AsyncClient).
"""
from __future__ import annotations

import abc
import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


class PaymentProviderError(RuntimeError):
    """Ошибка взаимодействия с платёжным провайдером."""

    def __init__(self, message: str, *, status_code: int | None = None, payload: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


@dataclass(frozen=True, slots=True)
class PaymentMethodInfo:
    code: int
    name: str
    label_ru: str
    label_en: str
    text_key: str
    is_crypto: bool = False

    def label(self, locale: str = "ru") -> str:
        return self.label_en if locale.lower().startswith("en") else self.label_ru


# Коды по Platega: paymentMethod enum = 2, 3, 11, 12, 13, 14.
# Надписи на кнопках переопределяются в админке (Тексты → payment.*).
METHODS: dict[int, PaymentMethodInfo] = {
    2: PaymentMethodInfo(2, "SBP_QR", "СБП QR", "SBP QR", "payment.method.sbp_qr"),
    3: PaymentMethodInfo(3, "CARD_C2C", "Банковская карта", "Bank card", "payment.method.card_c2c"),
    11: PaymentMethodInfo(11, "SBP_H2H", "СБП", "SBP", "payment.method.sbp"),
    12: PaymentMethodInfo(12, "CARD_H2H", "Карта (H2H)", "Card (H2H)", "payment.method.card_h2h"),
    13: PaymentMethodInfo(
        13, "CRYPTO", "Криптовалюта (USDT)", "Crypto (USDT)", "payment.method.crypto", True
    ),
    14: PaymentMethodInfo(14, "CARD_INTL", "Международная карта", "International card", "payment.method.card_intl"),
}

DEFAULT_METHODS: tuple[int, ...] = (2, 13)

# Коды статусов для POST /transaction/export/excel.
# ВНИМАНИЕ: в документации Platega явно не раскрыты — требует подтверждения у менеджера.
EXPORT_STATUS_CODES: dict[str, str] = {
    "PENDING": "1",
    "CONFIRMED": "6",
    "CANCELED": "7",
}

# Статусы транзакций Platega.
PROVIDER_STATUS_PENDING = "PENDING"
PROVIDER_STATUS_CONFIRMED = "CONFIRMED"
PROVIDER_STATUS_CANCELED = "CANCELED"
PROVIDER_STATUS_CHARGEBACKED = "CHARGEBACKED"
KNOWN_PROVIDER_STATUSES = (
    PROVIDER_STATUS_PENDING,
    PROVIDER_STATUS_CONFIRMED,
    PROVIDER_STATUS_CANCELED,
    PROVIDER_STATUS_CHARGEBACKED,
)


@dataclass(slots=True)
class ProviderStatus:
    """Состояние подключения провайдера — показывается в админке."""

    provider: str
    configured: bool
    enabled: bool
    test_mode: bool = False
    methods: list[int] = field(default_factory=list)
    merchant_id_masked: str | None = None
    message: str | None = None


@dataclass(slots=True)
class CreatePaymentRequest:
    order_no: str
    amount: Decimal
    currency: str
    method_code: int
    description: str
    return_url: str
    failed_url: str
    user_id: str | None = None
    user_name: str | None = None
    payload: str | None = None
    client_ip: str | None = None


@dataclass(slots=True)
class CreatePaymentResult:
    transaction_id: str
    status: str
    redirect_url: str | None = None
    method_code: int | None = None
    amount: Decimal | None = None
    currency: str | None = None
    expires_at: dt.datetime | None = None
    usdt_rate: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PaymentStatusResult:
    transaction_id: str
    status: str
    amount: Decimal | None = None
    currency: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WebhookResult:
    """Разобранное вебхук-событие."""

    transaction_id: str | None
    status: str | None
    event_key: str
    signature_valid: bool
    order_no: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ExportRequest:
    statuses: list[str]
    payment_methods: list[int]
    date_from: dt.datetime
    date_to: dt.datetime
    timezone_id: str = "Europe/Moscow"


@dataclass(slots=True)
class ExportResult:
    url: str | None
    raw: dict[str, Any] = field(default_factory=dict)


class PaymentProvider(abc.ABC):
    """Единый интерфейс для всех платёжных систем."""

    name: str = "base"

    @abc.abstractmethod
    async def status(self) -> ProviderStatus:
        """Готов ли провайдер к работе."""

    @abc.abstractmethod
    async def create_payment(self, req: CreatePaymentRequest) -> CreatePaymentResult:
        """Создать транзакцию и получить ссылку на оплату."""

    @abc.abstractmethod
    async def get_status(self, transaction_id: str) -> PaymentStatusResult:
        """Источник истины по статусу платежа."""

    async def parse_webhook(
        self, *, payload: dict[str, Any], headers: dict[str, str], raw_body: bytes = b""
    ) -> WebhookResult:
        """Разобрать callback. По умолчанию — без проверки подписи."""
        txn = str(payload.get("transactionId") or payload.get("id") or "") or None
        status = payload.get("status")
        return WebhookResult(
            transaction_id=txn,
            status=str(status) if status else None,
            event_key=f"{txn or 'unknown'}:{status or 'unknown'}",
            signature_valid=False,
            order_no=payload.get("payload") or None,
            raw=payload,
        )

    async def export_excel(self, req: ExportRequest) -> ExportResult:
        raise PaymentProviderError("Экспорт не поддерживается этим провайдером")

    def methods(self) -> list[PaymentMethodInfo]:
        return list(METHODS.values())

    async def aclose(self) -> None:
        """Закрыть сетевые ресурсы."""
        return None


def normalize_status(value: str | None) -> str:
    """Привести статус провайдера к верхнему регистру без пробелов."""
    return (value or "").strip().upper()
