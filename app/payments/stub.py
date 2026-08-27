"""Локальная заглушка платежеи́ (ТЗ: проверка всего сценария без реальных денег).

Создаёт виртуальную транзакцию и отдаёт ссылку на встроенную страницу
/payments/sandbox/{transaction_id}, где можно нажать «Оплатить» или «Отменить».
Состояние хранится в памяти процесса — этого достаточно для отладки.
"""
from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import structlog

from app.payments.base import (
    METHODS,
    CreatePaymentRequest,
    CreatePaymentResult,
    ExportRequest,
    ExportResult,
    PaymentProvider,
    PaymentProviderError,
    PaymentStatusResult,
    ProviderStatus,
    WebhookResult,
    normalize_status,
)

log = structlog.get_logger(__name__)


class StubProvider(PaymentProvider):
    name = "stub"

    def __init__(
        self,
        *,
        base_url: str = "",
        auto_confirm: bool = False,
        ttl_seconds: int = 900,
        methods: list[int] | None = None,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.auto_confirm = auto_confirm
        self.ttl_seconds = int(ttl_seconds or 900)
        self.allowed_methods = [int(m) for m in (methods or []) if int(m) in METHODS]
        self._transactions: dict[str, dict[str, Any]] = {}

    async def status(self) -> ProviderStatus:
        return ProviderStatus(
            provider=self.name,
            configured=True,
            enabled=True,
            test_mode=True,
            methods=self.allowed_methods or list(METHODS.keys()),
            merchant_id_masked="sandbox",
            message="Тестовая заглушка: реальные деньги не списываются",
        )

    def methods(self):
        if not self.allowed_methods:
            return list(METHODS.values())
        return [METHODS[c] for c in self.allowed_methods if c in METHODS]

    async def create_payment(self, req: CreatePaymentRequest) -> CreatePaymentResult:
        txn = uuid.uuid4().hex
        expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=self.ttl_seconds)
        status = "CONFIRMED" if self.auto_confirm else "PENDING"
        self._transactions[txn] = {
            "status": status,
            "amount": str(req.amount),
            "currency": req.currency,
            "order_no": req.order_no,
            "expires_at": expires_at.isoformat(),
        }
        log.info("stub_payment.created", txn=txn, order_no=req.order_no, status=status)
        return CreatePaymentResult(
            transaction_id=txn,
            status=status,
            redirect_url=f"{self.base_url}/payments/sandbox/{txn}",
            method_code=req.method_code,
            amount=req.amount,
            currency=req.currency,
            expires_at=expires_at,
            raw={"provider": "stub", "order_no": req.order_no},
        )

    async def get_status(self, transaction_id: str) -> PaymentStatusResult:
        data = self._transactions.get(transaction_id)
        if data is None:
            return PaymentStatusResult(transaction_id=transaction_id, status="CANCELED", raw={})
        return PaymentStatusResult(
            transaction_id=transaction_id,
            status=normalize_status(data["status"]),
            amount=Decimal(data["amount"]),
            currency=data["currency"],
            raw=dict(data),
        )

    async def parse_webhook(
        self, *, payload: dict[str, Any], headers: dict[str, str], raw_body: bytes = b""
    ) -> WebhookResult:
        txn = str(payload.get("transactionId") or "") or None
        status = normalize_status(payload.get("status")) or None
        if txn and txn in self._transactions and status:
            self._transactions[txn]["status"] = status
        return WebhookResult(
            transaction_id=txn,
            status=status,
            event_key=f"{txn or 'unknown'}:{status or 'unknown'}",
            signature_valid=True,
            order_no=payload.get("payload"),
            raw=payload,
        )

    async def export_excel(self, req: ExportRequest) -> ExportResult:
        raise PaymentProviderError("В тестовом режиме выгрузка Excel недоступна")

    # ---- служебное для песочницы -------------------------------------
    def set_status(self, transaction_id: str, status: str) -> bool:
        if transaction_id not in self._transactions:
            return False
        self._transactions[transaction_id]["status"] = normalize_status(status)
        return True

    def get_transaction(self, transaction_id: str) -> dict[str, Any] | None:
        data = self._transactions.get(transaction_id)
        return dict(data) if data else None
