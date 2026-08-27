"""Интеграция с Platega (https://app.platega.io).

Эндпоинты:
* POST /transaction/process        — создание транзакции
* GET  /transaction/{id}           — статус (источник истины, т.к. спецификации webhook нет)
* POST /transaction/export/excel   — выгрузка реестра

Авторизация: заголовки X-MerchantId и X-Secret.
Секреты никогда не попадают в логи и в raw_response.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hmac
import json
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Any

import httpx
import structlog

from app.payments.base import (
    METHODS,
    CreatePaymentRequest,
    CreatePaymentResult,
    ExportRequest,
    ExportResult,
    EXPORT_STATUS_CODES,
    PaymentProvider,
    PaymentProviderError,
    PaymentStatusResult,
    ProviderStatus,
    WebhookResult,
    normalize_status,
)

log = structlog.get_logger(__name__)

DEFAULT_BASE_URL = "https://app.platega.io"
RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY = 0.4


def _mask(value: str | None, visible: int = 4) -> str | None:
    if not value:
        return None
    if len(value) <= visible:
        return "*" * len(value)
    return f"{'*' * (len(value) - visible)}{value[-visible:]}"


def _parse_expires_in(value: Any) -> dt.timedelta | None:
    """expiresIn приходит в виде '00:15:00'."""
    if not value:
        return None
    parts = str(value).split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    while len(nums) < 3:
        nums.append(0)
    return dt.timedelta(hours=nums[0], minutes=nums[1], seconds=nums[2])


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


class PlategaProvider(PaymentProvider):
    name = "platega"

    def __init__(
        self,
        *,
        merchant_id: str,
        secret: str,
        base_url: str = DEFAULT_BASE_URL,
        enabled: bool = True,
        test_mode: bool = False,
        methods: list[int] | None = None,
        ttl_seconds: int = 900,
        send_metadata: bool = True,
        webhook_secret: str | None = None,
        webhook_require_signature: bool = False,
        timeout: float = 20.0,
    ) -> None:
        self.merchant_id = (merchant_id or "").strip()
        self.secret = (secret or "").strip()
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.enabled = enabled
        self.test_mode = test_mode
        self.allowed_methods = [int(m) for m in (methods or []) if int(m) in METHODS]
        self.ttl_seconds = int(ttl_seconds or 900)
        self.send_metadata = send_metadata
        self.webhook_secret = (webhook_secret or "").strip() or None
        self.webhook_require_signature = webhook_require_signature
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    #  HTTP
    # ------------------------------------------------------------------
    @property
    def configured(self) -> bool:
        return bool(self.merchant_id and self.secret)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            async with self._lock:
                if self._client is None or self._client.is_closed:
                    self._client = httpx.AsyncClient(
                        base_url=self.base_url,
                        timeout=httpx.Timeout(self._timeout),
                        headers={
                            "X-MerchantId": self.merchant_id,
                            "X-Secret": self.secret,
                            "Content-Type": "application/json",
                            "Accept": "application/json",
                        },
                        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
                    )
        return self._client

    async def _request(
        self, method: str, path: str, *, json_body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if not self.configured:
            raise PaymentProviderError("Platega не настроена: укажите MerchantId и Secret")
        client = await self._get_client()
        last_error: Exception | None = None
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                response = await client.request(method, path, json=json_body)
            except httpx.HTTPError as exc:  # сетевые сбои — повторяем
                last_error = exc
                log.warning("platega.network_error", attempt=attempt, path=path, error=str(exc))
                if attempt == RETRY_ATTEMPTS:
                    break
                await asyncio.sleep(RETRY_BASE_DELAY * attempt)
                continue

            if response.status_code >= 500 and attempt < RETRY_ATTEMPTS:
                log.warning("platega.server_error", attempt=attempt, status=response.status_code)
                await asyncio.sleep(RETRY_BASE_DELAY * attempt)
                continue

            try:
                data = response.json()
            except (json.JSONDecodeError, ValueError):
                data = {"raw_text": response.text[:2000]}

            if response.status_code >= 400:
                raise PaymentProviderError(
                    f"Platega {method} {path} → HTTP {response.status_code}",
                    status_code=response.status_code,
                    payload=data,
                )
            return data if isinstance(data, dict) else {"result": data}

        raise PaymentProviderError(f"Platega недоступна: {last_error}")

    # ------------------------------------------------------------------
    #  API провайдера
    # ------------------------------------------------------------------
    async def status(self) -> ProviderStatus:
        return ProviderStatus(
            provider=self.name,
            configured=self.configured,
            enabled=self.enabled,
            test_mode=self.test_mode,
            methods=self.allowed_methods or [],
            merchant_id_masked=_mask(self.merchant_id, 6),
            message=None if self.configured else "Не заполнены MerchantId / Secret",
        )

    def methods(self):
        if not self.allowed_methods:
            return list(METHODS.values())
        return [METHODS[c] for c in self.allowed_methods if c in METHODS]

    async def create_payment(self, req: CreatePaymentRequest) -> CreatePaymentResult:
        if self.allowed_methods and req.method_code not in self.allowed_methods:
            raise PaymentProviderError(f"Метод оплаты {req.method_code} отключён в настройках")

        # Поле id НЕ передаётся — его генерирует Platega.
        body: dict[str, Any] = {
            "paymentMethod": int(req.method_code),
            "paymentDetails": {
                "amount": float(req.amount),
                "currency": req.currency,
            },
            "description": req.description[:255],
            "return": req.return_url,
            "failedUrl": req.failed_url,
        }
        if req.payload:
            body["payload"] = req.payload
        # Без metadata.userId отключается антифрод — передаём всегда, когда есть данные.
        if self.send_metadata and req.user_id:
            body["metadata"] = {
                "userId": str(req.user_id),
                "userName": str(req.user_name or req.user_id),
            }

        data = await self._request("POST", "/transaction/process", json_body=body)

        txn = str(data.get("transactionId") or data.get("id") or "")
        if not txn:
            raise PaymentProviderError("Platega не вернула transactionId", payload=data)

        details = data.get("paymentDetails") or {}
        delta = _parse_expires_in(data.get("expiresIn")) or dt.timedelta(seconds=self.ttl_seconds)
        return CreatePaymentResult(
            transaction_id=txn,
            status=normalize_status(data.get("status")) or "PENDING",
            redirect_url=data.get("redirect") or None,
            method_code=int(data.get("paymentMethod") or req.method_code),
            amount=_to_decimal(details.get("amount")) or req.amount,
            currency=str(details.get("currency") or req.currency),
            expires_at=dt.datetime.now(dt.UTC) + delta,
            usdt_rate=str(data.get("usdtRate")) if data.get("usdtRate") is not None else None,
            raw=self._safe_raw(data),
        )

    async def get_status(self, transaction_id: str) -> PaymentStatusResult:
        data = await self._request("GET", f"/transaction/{transaction_id}")
        details = data.get("paymentDetails") or {}
        return PaymentStatusResult(
            transaction_id=str(data.get("transactionId") or transaction_id),
            status=normalize_status(data.get("status")),
            amount=_to_decimal(details.get("amount")),
            currency=str(details.get("currency")) if details.get("currency") else None,
            raw=self._safe_raw(data),
        )

    async def parse_webhook(
        self, *, payload: dict[str, Any], headers: dict[str, str], raw_body: bytes = b""
    ) -> WebhookResult:
        txn = str(payload.get("transactionId") or payload.get("id") or "") or None
        status = normalize_status(payload.get("status")) or None
        signature_valid = self._check_signature(headers=headers, raw_body=raw_body)
        return WebhookResult(
            transaction_id=txn,
            status=status,
            event_key=f"{txn or 'unknown'}:{status or 'unknown'}",
            signature_valid=signature_valid,
            order_no=(payload.get("payload") or None),
            raw=self._safe_raw(payload),
        )

    async def export_excel(self, req: ExportRequest) -> ExportResult:
        statuses: list[str] = []
        for raw_status in req.statuses:
            code = EXPORT_STATUS_CODES.get(normalize_status(raw_status))
            if code:
                statuses.append(code)
        body = {
            "statuses": statuses,
            "paymentMethods": [int(m) for m in req.payment_methods],
            "from": req.date_from.strftime("%Y-%m-%dT%H:%M:%S"),
            "to": req.date_to.strftime("%Y-%m-%dT%H:%M:%S"),
            "timeZoneId": req.timezone_id,
        }
        data = await self._request("POST", "/transaction/export/excel", json_body=body)
        return ExportResult(url=data.get("url"), raw=self._safe_raw(data))

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    # ------------------------------------------------------------------
    #  Вспомогательное
    # ------------------------------------------------------------------
    def _check_signature(self, *, headers: dict[str, str], raw_body: bytes) -> bool:
        """HMAC-SHA256 по телу запроса, если задан webhook_secret."""
        if not self.webhook_secret or not raw_body:
            return False
        lowered = {k.lower(): v for k, v in headers.items()}
        provided = (
            lowered.get("x-signature")
            or lowered.get("x-platega-signature")
            or lowered.get("signature")
        )
        if not provided:
            return False
        expected = hmac.new(self.webhook_secret.encode(), raw_body, sha256).hexdigest()
        return hmac.compare_digest(expected, provided.strip().lower())

    @staticmethod
    def _safe_raw(data: dict[str, Any]) -> dict[str, Any]:
        """Убираем всё, что может быть секретом, перед записью в БД."""
        if not isinstance(data, dict):
            return {}
        forbidden = {"secret", "x-secret", "apikey", "api_key", "token", "password"}
        return {k: v for k, v in data.items() if k.lower() not in forbidden}
