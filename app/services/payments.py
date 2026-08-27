"""Бизнес-логика платежей (ТЗ п.20-п.23, п.51).

Слой не знает про Platega напрямую — только про интерфейс PaymentProvider.
Источник истины по статусу — GET /transaction/{id} (sync_payment), webhook лишь
ускоряет реакцию. Повторные события гасятся UNIQUE(provider, event_key).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Sequence

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.payments import base as pay_base
from app.payments import get_provider, method_label
from app.models import (
    Order,
    OrderStatus,
    Payment,
    PaymentEvent,
    PaymentStatus,
)
from app.services import cms, orders as orders_service, promo as promo_service

log = structlog.get_logger(__name__)

# Провайдер → наш статус
STATUS_MAP: dict[str, PaymentStatus] = {
    pay_base.PROVIDER_STATUS_PENDING: PaymentStatus.PENDING,
    pay_base.PROVIDER_STATUS_CONFIRMED: PaymentStatus.CONFIRMED,
    pay_base.PROVIDER_STATUS_CANCELED: PaymentStatus.CANCELED,
    pay_base.PROVIDER_STATUS_CHARGEBACKED: PaymentStatus.CHARGEBACKED,
}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


@dataclass(slots=True)
class PaymentStart:
    """Результат создания платежа для UI бота."""

    ok: bool
    payment: Payment | None = None
    redirect_url: str | None = None
    error: str | None = None


@dataclass(slots=True)
class SyncResult:
    """Изменился ли статус и что делать дальше."""

    changed: bool
    status: PaymentStatus
    just_paid: bool = False
    failed: bool = False
    error: str | None = None


async def _base_url(db: AsyncSession) -> str:
    """Публичный адрес для return/failed ссылок: сначала админка, потом .env."""
    domain = str(await cms.setting(db, "shop.domain", "") or "").strip().rstrip("/")
    if domain:
        return domain if domain.startswith("http") else "https://" + domain
    try:
        from app.core.config import settings

        fallback = str(getattr(settings, "public_base_url", "") or "").rstrip("/")
    except Exception:  # pragma: no cover
        fallback = ""
    return fallback or "http://localhost:8000"


# =====================================================================
#  СОЗДАНИЕ ПЛАТЕЖА
# =====================================================================
async def start_payment(
    db: AsyncSession,
    *,
    order: Order,
    method_code: int,
    client_ip: str | None = None,
) -> PaymentStart:
    if order.status in orders_service.TERMINAL_STATUSES:
        return PaymentStart(ok=False, error="order_terminal")

    # Если есть живой платёж тем же методом — отдаём старую ссылку.
    existing = await orders_service.active_payment(db, order.id)
    if existing is not None and existing.method_code == int(method_code):
        if existing.expires_at is None or existing.expires_at > _now():
            return PaymentStart(ok=True, payment=existing, redirect_url=existing.redirect_url)

    provider = await get_provider(db)
    status = await provider.status()
    if not status.enabled:
        return PaymentStart(ok=False, error="payments_disabled")
    if not status.configured:
        return PaymentStart(ok=False, error="provider_not_configured")
    if status.methods and int(method_code) not in status.methods:
        return PaymentStart(ok=False, error="method_not_allowed")

    base_url = await _base_url(db)
    user = order.__dict__.get("user")
    tg_id = getattr(user, "tg_id", None)
    user_name = getattr(user, "username", None) or getattr(user, "first_name", None)
    info = pay_base.METHODS.get(int(method_code))

    request = pay_base.CreatePaymentRequest(
        order_no=order.public_no,
        amount=Decimal(order.total),
        currency=order.currency or "RUB",
        method_code=int(method_code),
        description=f"{order.product_title} ({order.public_no})"[:255],
        return_url=f"{base_url}/payments/return/{order.public_no}",
        failed_url=f"{base_url}/payments/failed/{order.public_no}",
        user_id=str(tg_id) if tg_id else str(order.user_id),
        user_name=str(user_name) if user_name else None,
        payload=order.public_no,
        client_ip=client_ip,
    )

    payment = Payment(
        order_id=order.id,
        provider=getattr(provider, "name", "platega"),
        method_code=int(method_code),
        method_name=info.name if info else None,
        amount=Decimal(order.total),
        currency=order.currency or "RUB",
        status=PaymentStatus.PENDING,
    )

    try:
        result = await provider.create_payment(request)
    except pay_base.PaymentProviderError as exc:
        payment.status = PaymentStatus.ERROR
        payment.error_message = str(exc)[:500]
        db.add(payment)
        await db.commit()
        log.warning("payment.create_failed", order_no=order.public_no, error=str(exc))
        return PaymentStart(ok=False, payment=payment, error="provider_error")

    payment.provider_txn_id = result.transaction_id
    payment.redirect_url = result.redirect_url
    payment.expires_at = result.expires_at
    payment.raw_response = result.raw or {}
    payment.status = STATUS_MAP.get(
        pay_base.normalize_status(result.status), PaymentStatus.PENDING
    )

    db.add(payment)
    try:
        await db.commit()
    except IntegrityError:
        # Такая транзакция уже записана (параллельный запрос) — берём её.
        await db.rollback()
        found = await find_payment_by_txn(db, result.transaction_id)
        if found is not None:
            return PaymentStart(ok=True, payment=found, redirect_url=found.redirect_url)
        raise

    await db.refresh(payment)
    await orders_service.mark_payment_pending(db, order)
    log.info(
        "payment.created",
        order_no=order.public_no,
        txn=result.transaction_id,
        method=method_code,
    )
    return PaymentStart(ok=True, payment=payment, redirect_url=result.redirect_url)


# =====================================================================
#  СИНХРОНИЗАЦИЯ СТАТУСОВ
# =====================================================================
async def apply_status(db: AsyncSession, payment: Payment, provider_status: str) -> SyncResult:
    """Применить статус провайдера к платежу и заказу."""
    normalized = pay_base.normalize_status(provider_status)
    mapped = STATUS_MAP.get(normalized)
    if mapped is None:
        log.warning("payment.unknown_status", status=normalized, payment_id=payment.id)
        return SyncResult(changed=False, status=payment.status, error="unknown_status")

    payment.last_checked_at = _now()
    payment.check_attempts = int(payment.check_attempts or 0) + 1

    if payment.status == mapped:
        await db.commit()
        return SyncResult(changed=False, status=mapped)

    payment.status = mapped
    if mapped == PaymentStatus.CONFIRMED:
        payment.confirmed_at = payment.confirmed_at or _now()
    await db.commit()

    order = await db.get(Order, payment.order_id)
    if order is None:
        return SyncResult(changed=True, status=mapped)

    if mapped == PaymentStatus.CONFIRMED:
        just_paid = await orders_service.mark_paid(db, order)
        if just_paid:
            await promo_service.register_usage_for_order(db, order)
        return SyncResult(changed=True, status=mapped, just_paid=just_paid)

    if mapped in {PaymentStatus.CANCELED, PaymentStatus.EXPIRED, PaymentStatus.ERROR}:
        if order.status in {OrderStatus.CREATED, OrderStatus.PAYMENT_PENDING}:
            await orders_service.mark_failed(db, order, reason=f"payment_{mapped.value}")
            return SyncResult(changed=True, status=mapped, failed=True)

    return SyncResult(changed=True, status=mapped)


async def sync_payment(db: AsyncSession, payment: Payment) -> SyncResult:
    """Спросить статус у провайдера и применить его."""
    if not payment.provider_txn_id:
        return SyncResult(changed=False, status=payment.status, error="no_transaction")

    if (
        payment.status == PaymentStatus.PENDING
        and payment.expires_at is not None
        and payment.expires_at < _now()
    ):
        payment.status = PaymentStatus.EXPIRED
        payment.last_checked_at = _now()
        await db.commit()
        order = await db.get(Order, payment.order_id)
        if order is not None and order.status in {
            OrderStatus.CREATED,
            OrderStatus.PAYMENT_PENDING,
        }:
            await orders_service.mark_failed(db, order, reason="payment_expired")
        return SyncResult(changed=True, status=PaymentStatus.EXPIRED, failed=True)

    provider = await get_provider(db)
    try:
        result = await provider.get_status(payment.provider_txn_id)
    except pay_base.PaymentProviderError as exc:
        payment.last_checked_at = _now()
        payment.check_attempts = int(payment.check_attempts or 0) + 1
        payment.error_message = str(exc)[:500]
        await db.commit()
        return SyncResult(changed=False, status=payment.status, error="provider_error")

    if result.raw:
        payment.raw_response = result.raw
    return await apply_status(db, payment, result.status)


async def sync_order_payment(db: AsyncSession, order: Order) -> SyncResult:
    """Кнопка «Проверить оплату» в боте."""
    stmt = (
        select(Payment)
        .where(Payment.order_id == order.id)
        .order_by(Payment.id.desc())
        .limit(1)
    )
    payment = (await db.execute(stmt)).scalar_one_or_none()
    if payment is None:
        return SyncResult(changed=False, status=PaymentStatus.PENDING, error="no_payment")
    return await sync_payment(db, payment)


# =====================================================================
#  WEBHOOK
# =====================================================================
async def register_event(
    db: AsyncSession, *, result: pay_base.WebhookResult, provider_name: str = "platega"
) -> PaymentEvent | None:
    """Записать событие. None = дубликат (уже обработан)."""
    payment = (
        await find_payment_by_txn(db, result.transaction_id)
        if result.transaction_id
        else None
    )
    if payment is None and result.order_no:
        payment = await find_payment_by_order_no(db, result.order_no)

    event = PaymentEvent(
        provider=provider_name,
        event_key=result.event_key[:255],
        payment_id=payment.id if payment else None,
        status_reported=(result.status or "")[:32] or None,
        payload=result.raw or {},
        signature_valid=bool(result.signature_valid),
        processed=False,
    )
    db.add(event)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        log.info("payment.event_duplicate", event_key=result.event_key)
        return None
    await db.refresh(event)
    return event


async def mark_event_processed(db: AsyncSession, event: PaymentEvent) -> None:
    event.processed = True
    await db.commit()


async def handle_webhook_event(
    db: AsyncSession, *, event: PaymentEvent
) -> SyncResult | None:
    """После webhook всегда переспрашиваем статус у API — не верим телу запроса."""
    if event.payment_id is None:
        await mark_event_processed(db, event)
        return None
    payment = await db.get(Payment, event.payment_id)
    if payment is None:
        await mark_event_processed(db, event)
        return None
    result = await sync_payment(db, payment)
    await mark_event_processed(db, event)
    return result


async def find_payment_by_txn(db: AsyncSession, txn_id: str | None) -> Payment | None:
    if not txn_id:
        return None
    stmt = select(Payment).where(Payment.provider_txn_id == str(txn_id)).limit(1)
    return (await db.execute(stmt)).scalar_one_or_none()


async def find_payment_by_order_no(db: AsyncSession, order_no: str) -> Payment | None:
    stmt = (
        select(Payment)
        .join(Order, Order.id == Payment.order_id)
        .where(Order.public_no == str(order_no))
        .order_by(Payment.id.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


# =====================================================================
#  АДМИНКА: СПИСКИ, СТАТУС ПРОВАЙДЕРА, ЭКСПОРТ
# =====================================================================
async def list_payments(
    db: AsyncSession,
    *,
    status: str | None = None,
    query: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[Sequence[Payment], int]:
    stmt = select(Payment)
    count_stmt = select(func.count(Payment.id))
    conditions = []
    if status:
        conditions.append(Payment.status == PaymentStatus(status))
    if query:
        conditions.append(Payment.provider_txn_id.ilike(f"%{query.strip()}%"))
    for condition in conditions:
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    stmt = stmt.order_by(Payment.id.desc()).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().unique().all()
    total = int((await db.execute(count_stmt)).scalar() or 0)
    return rows, total


async def list_events(
    db: AsyncSession, *, limit: int = 100, offset: int = 0
) -> tuple[Sequence[PaymentEvent], int]:
    stmt = (
        select(PaymentEvent)
        .order_by(PaymentEvent.received_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(stmt)).scalars().unique().all()
    total = int((await db.execute(select(func.count(PaymentEvent.id)))).scalar() or 0)
    return rows, total


async def provider_status(db: AsyncSession) -> pay_base.ProviderStatus:
    provider = await get_provider(db)
    return await provider.status()


async def available_methods(db: AsyncSession, locale: str = "ru") -> list[dict[str, Any]]:
    """Методы, разрешённые в админке, с надписями из CMS."""
    status = await provider_status(db)
    codes = status.methods or list(pay_base.DEFAULT_METHODS)
    items: list[dict[str, Any]] = []
    for code in codes:
        info = pay_base.METHODS.get(int(code))
        if info is None:
            continue
        items.append(
            {
                "code": info.code,
                "name": info.name,
                "label": await method_label(db, info.code, locale),
                "is_crypto": info.is_crypto,
            }
        )
    return items


async def export_transactions_excel(
    db: AsyncSession,
    *,
    statuses: Sequence[str],
    payment_methods: Sequence[int],
    date_from: dt.datetime,
    date_to: dt.datetime,
    timezone_id: str = "Europe/Moscow",
) -> pay_base.ExportResult:
    provider = await get_provider(db)
    request = pay_base.ExportRequest(
        statuses=[pay_base.normalize_status(s) for s in statuses],
        payment_methods=[int(m) for m in payment_methods],
        date_from=date_from,
        date_to=date_to,
        timezone_id=timezone_id,
    )
    return await provider.export_excel(request)


async def payments_summary(db: AsyncSession, days: int = 30) -> dict[str, Any]:
    since = _now() - dt.timedelta(days=max(1, int(days)))
    stmt = (
        select(
            Payment.status,
            func.count(Payment.id),
            func.coalesce(func.sum(Payment.amount), 0),
        )
        .where(Payment.created_at >= since)
        .group_by(Payment.status)
    )
    rows = (await db.execute(stmt)).all()
    by_status = {
        str(status): {"count": int(count), "amount": Decimal(amount or 0)}
        for status, count, amount in rows
    }
    confirmed = by_status.get("confirmed", {"count": 0, "amount": Decimal("0")})
    total_count = sum(item["count"] for item in by_status.values())
    conversion = (confirmed["count"] / total_count * 100) if total_count else 0.0

    method_stmt = (
        select(
            Payment.method_code,
            func.count(Payment.id),
            func.coalesce(func.sum(Payment.amount), 0),
        )
        .where(Payment.created_at >= since, Payment.status == PaymentStatus.CONFIRMED)
        .group_by(Payment.method_code)
    )
    methods = []
    for code, count, amount in (await db.execute(method_stmt)).all():
        info = pay_base.METHODS.get(int(code)) if code is not None else None
        methods.append(
            {
                "code": int(code) if code is not None else None,
                "name": info.name if info else "—",
                "count": int(count),
                "amount": Decimal(amount or 0),
            }
        )

    return {
        "days": days,
        "by_status": by_status,
        "revenue": confirmed["amount"],
        "confirmed": confirmed["count"],
        "total": total_count,
        "conversion": round(conversion, 1),
        "methods": methods,
    }
