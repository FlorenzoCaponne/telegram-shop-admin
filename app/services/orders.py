"""Заказы и автовыдача (ТЗ п.19-п.28, п.50).

Сценарий: КАТЕГОРИЯ → ТОВАР → ЦЕНА → 💳 ОПЛАТИТЬ → ПЛАТЁЖ → АВТОВЫДАЧА.
Корзины и баланса нет — один заказ = один товар.

Защита от двойной выдачи:
* склад выбирается через SELECT ... FOR UPDATE SKIP LOCKED;
* перевод в completed выполняется условным UPDATE (compare-and-set).
"""
from __future__ import annotations

import datetime as dt
import secrets
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Sequence

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import NS_CATALOG, cache
from app.models import (
    DeliveryType,
    InventoryItem,
    InventoryStatus,
    Order,
    OrderStatus,
    Payment,
    PaymentStatus,
    Product,
    User,
)
from app.services import cms

log = structlog.get_logger(__name__)

TERMINAL_STATUSES: frozenset[OrderStatus] = frozenset(
    {OrderStatus.COMPLETED, OrderStatus.FAILED, OrderStatus.CANCELLED}
)
PAYABLE_STATUSES: frozenset[OrderStatus] = frozenset(
    {OrderStatus.CREATED, OrderStatus.PAYMENT_PENDING}
)
_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def generate_order_no() -> str:
    """Короткий человекочитаемый номер: 260827-K7Q3F."""
    stamp = _now().strftime("%y%m%d")
    tail = "".join(secrets.choice(_ALPHABET) for _ in range(5))
    return f"{stamp}-{tail}"


@dataclass(slots=True)
class DeliveryResult:
    ok: bool
    content: str | None = None
    manual: bool = False
    reason: str | None = None


# =====================================================================
#  СОЗДАНИЕ И СТАТУСЫ
# =====================================================================
async def create_order(
    db: AsyncSession,
    *,
    user: User,
    product: Product,
    locale: str = "ru",
    discount_amount: Decimal | None = None,
    promo_code_id: int | None = None,
    ttl_seconds: int = 900,
) -> Order:
    title = cms.pick_locale(product.title, locale) or product.slug
    discount = Decimal(discount_amount or 0)
    if discount < 0:
        discount = Decimal("0")
    if discount > product.price:
        discount = Decimal(product.price)
    total = (Decimal(product.price) - discount).quantize(Decimal("0.01"))

    order = Order(
        public_no=generate_order_no(),
        user_id=user.id,
        product_id=product.id,
        product_title=title[:255],
        product_emoji=(product.emoji or "🛍")[:16],
        price=Decimal(product.price),
        discount_amount=discount,
        total=total,
        currency=product.currency,
        status=OrderStatus.CREATED,
        promo_code_id=promo_code_id,
        locale=cms.normalize_locale(locale),
        expires_at=_now() + dt.timedelta(seconds=max(60, int(ttl_seconds))),
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)
    log.info("order.created", order_no=order.public_no, user_id=user.id, total=str(total))
    return order


async def mark_payment_pending(db: AsyncSession, order: Order) -> Order:
    if order.status == OrderStatus.CREATED:
        order.status = OrderStatus.PAYMENT_PENDING
        await db.commit()
    return order


async def mark_paid(db: AsyncSession, order: Order) -> bool:
    """Перевод в paid только из нефинальных статусов. True = перевели мы."""
    stmt = (
        update(Order)
        .where(
            Order.id == order.id,
            Order.status.in_([OrderStatus.CREATED, OrderStatus.PAYMENT_PENDING]),
        )
        .values(status=OrderStatus.PAID, paid_at=_now())
    )
    result = await db.execute(stmt)
    await db.commit()
    changed = bool(result.rowcount)
    if changed:
        await db.refresh(order)
        log.info("order.paid", order_no=order.public_no)
    return changed


async def mark_failed(db: AsyncSession, order: Order, reason: str = "") -> Order:
    if order.status in TERMINAL_STATUSES:
        return order
    order.status = OrderStatus.FAILED
    order.failure_reason = (reason or "")[:255] or None
    await db.commit()
    log.info("order.failed", order_no=order.public_no, reason=reason)
    return order


async def cancel_order(db: AsyncSession, order: Order, reason: str = "") -> Order:
    if order.status in TERMINAL_STATUSES:
        return order
    order.status = OrderStatus.CANCELLED
    order.failure_reason = (reason or "")[:255] or None
    await db.commit()
    await release_reserved(db, order)
    log.info("order.cancelled", order_no=order.public_no, reason=reason)
    return order


# =====================================================================
#  АВТОВЫДАЧА
# =====================================================================
async def deliver(db: AsyncSession, order: Order) -> DeliveryResult:
    """Выдать товар. Идемпотентно: повторный вызов не спишет вторую единицу."""
    if order.status == OrderStatus.COMPLETED and order.delivered_content:
        return DeliveryResult(ok=True, content=order.delivered_content)
    if order.status in {OrderStatus.CANCELLED, OrderStatus.FAILED}:
        return DeliveryResult(ok=False, reason="order_terminal")

    # Занять заказ на время выдачи (compare-and-set).
    claim = await db.execute(
        update(Order)
        .where(Order.id == order.id, Order.status == OrderStatus.PAID)
        .values(status=OrderStatus.PROCESSING)
    )
    await db.commit()
    if not claim.rowcount:
        await db.refresh(order)
        if order.status == OrderStatus.PROCESSING:
            return DeliveryResult(ok=False, reason="already_processing")
        if order.status == OrderStatus.COMPLETED:
            return DeliveryResult(ok=True, content=order.delivered_content)
        return DeliveryResult(ok=False, reason="order_not_paid")

    product = await db.get(Product, order.product_id) if order.product_id else None
    if product is None:
        order.status = OrderStatus.PAID
        await db.commit()
        return DeliveryResult(ok=False, reason="product_missing")

    if product.delivery_type == DeliveryType.MANUAL:
        order.status = OrderStatus.PAID
        await db.commit()
        return DeliveryResult(ok=False, manual=True, reason="manual_delivery")

    content: str | None = None

    if product.delivery_type == DeliveryType.STATIC_TEXT:
        payload = product.delivery_payload or {}
        content = cms.pick_locale(payload.get("text"), order.locale)
        if not content:
            order.status = OrderStatus.PAID
            await db.commit()
            return DeliveryResult(ok=False, manual=True, reason="empty_payload")

    elif product.delivery_type == DeliveryType.INVENTORY:
        stmt = (
            select(InventoryItem)
            .where(
                InventoryItem.product_id == product.id,
                InventoryItem.status == InventoryStatus.AVAILABLE,
            )
            .order_by(InventoryItem.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        item = (await db.execute(stmt)).scalar_one_or_none()
        if item is None:
            order.status = OrderStatus.PAID
            await db.commit()
            log.warning("order.out_of_stock", order_no=order.public_no, product_id=product.id)
            return DeliveryResult(ok=False, manual=True, reason="out_of_stock")
        item.status = InventoryStatus.DELIVERED
        item.order_id = order.id
        item.user_id = order.user_id
        item.reserved_at = item.reserved_at or _now()
        item.delivered_at = _now()
        content = item.content
        await cache.bump(NS_CATALOG)

    order.delivered_content = content
    order.status = OrderStatus.COMPLETED
    order.completed_at = _now()

    product.sales_count = int(product.sales_count or 0) + 1
    user = await db.get(User, order.user_id)
    if user is not None:
        user.orders_count = int(user.orders_count or 0) + 1
        user.total_spent = Decimal(user.total_spent or 0) + Decimal(order.total)

    await db.commit()
    log.info("order.delivered", order_no=order.public_no, product_id=product.id)
    return DeliveryResult(ok=True, content=content)


async def release_reserved(db: AsyncSession, order: Order) -> int:
    """Вернуть на склад единицы, зарезервированные под отменённый заказ."""
    stmt = (
        update(InventoryItem)
        .where(
            InventoryItem.order_id == order.id,
            InventoryItem.status == InventoryStatus.RESERVED,
        )
        .values(status=InventoryStatus.AVAILABLE, order_id=None, reserved_at=None)
    )
    result = await db.execute(stmt)
    await db.commit()
    if result.rowcount:
        await cache.bump(NS_CATALOG)
    return int(result.rowcount or 0)


async def deliver_manually(db: AsyncSession, order: Order, content: str) -> Order:
    """Ручная выдача из админки."""
    order.delivered_content = content
    order.status = OrderStatus.COMPLETED
    order.completed_at = _now()
    order.paid_at = order.paid_at or _now()
    await db.commit()
    return order


# =====================================================================
#  ЧТЕНИЕ
# =====================================================================
async def get_order(db: AsyncSession, order_id: int) -> Order | None:
    return await db.get(Order, order_id)


async def get_order_by_no(db: AsyncSession, public_no: str) -> Order | None:
    return (
        await db.execute(select(Order).where(Order.public_no == public_no))
    ).scalar_one_or_none()


async def list_user_orders(
    db: AsyncSession, user_id: int, *, limit: int = 10, offset: int = 0
) -> Sequence[Order]:
    stmt = (
        select(Order)
        .where(Order.user_id == user_id)
        .order_by(Order.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return (await db.execute(stmt)).scalars().unique().all()


async def list_orders(
    db: AsyncSession,
    *,
    status: str | None = None,
    query: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[Sequence[Order], int]:
    stmt = select(Order)
    count_stmt = select(func.count(Order.id))
    conditions = []
    if status:
        conditions.append(Order.status == OrderStatus(status))
    if query:
        conditions.append(Order.public_no.ilike(f"%{query.strip()}%"))
    for condition in conditions:
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    stmt = stmt.order_by(Order.created_at.desc()).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().unique().all()
    total = int((await db.execute(count_stmt)).scalar() or 0)
    return rows, total


async def active_payment(db: AsyncSession, order_id: int) -> Payment | None:
    stmt = (
        select(Payment)
        .where(Payment.order_id == order_id, Payment.status == PaymentStatus.PENDING)
        .order_by(Payment.id.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def find_pending_payments(db: AsyncSession, limit: int = 50) -> Sequence[Payment]:
    """Для воркера поллинга статусов — источник истины по платежам."""
    stmt = (
        select(Payment)
        .where(
            Payment.status == PaymentStatus.PENDING,
            Payment.provider_txn_id.is_not(None),
        )
        .order_by(Payment.last_checked_at.is_(None).desc(), Payment.last_checked_at)
        .limit(limit)
    )
    return (await db.execute(stmt)).scalars().unique().all()


async def find_expired_orders(db: AsyncSession, limit: int = 50) -> Sequence[Order]:
    stmt = (
        select(Order)
        .where(
            Order.status.in_([OrderStatus.CREATED, OrderStatus.PAYMENT_PENDING]),
            Order.expires_at.is_not(None),
            Order.expires_at < _now(),
        )
        .order_by(Order.expires_at)
        .limit(limit)
    )
    return (await db.execute(stmt)).scalars().unique().all()


async def find_stuck_paid_orders(db: AsyncSession, limit: int = 50) -> Sequence[Order]:
    """Оплачено, но не выдано — повторная попытка автовыдачи."""
    stmt = (
        select(Order)
        .where(Order.status == OrderStatus.PAID)
        .order_by(Order.paid_at)
        .limit(limit)
    )
    return (await db.execute(stmt)).scalars().unique().all()


# =====================================================================
#  СКЛАД
# =====================================================================
async def bulk_add_inventory(db: AsyncSession, product_id: int, lines: str | Sequence[str]) -> int:
    """Загрузить единицы склада списком (одна строка = один товар)."""
    if isinstance(lines, str):
        items = [line.strip() for line in lines.splitlines()]
    else:
        items = [str(line).strip() for line in lines]
    items = [line for line in items if line]
    if not items:
        return 0
    db.add_all(
        [
            InventoryItem(
                product_id=product_id,
                content=line,
                status=InventoryStatus.AVAILABLE,
            )
            for line in items
        ]
    )
    await db.commit()
    await cache.bump(NS_CATALOG)
    return len(items)


async def list_inventory(
    db: AsyncSession,
    *,
    product_id: int | None = None,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[Sequence[InventoryItem], int]:
    stmt = select(InventoryItem)
    count_stmt = select(func.count(InventoryItem.id))
    conditions = []
    if product_id:
        conditions.append(InventoryItem.product_id == product_id)
    if status:
        conditions.append(InventoryItem.status == InventoryStatus(status))
    for condition in conditions:
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    stmt = stmt.order_by(InventoryItem.id.desc()).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().unique().all()
    total = int((await db.execute(count_stmt)).scalar() or 0)
    return rows, total


async def delete_inventory_item(db: AsyncSession, item_id: int) -> bool:
    item = await db.get(InventoryItem, item_id)
    if item is None or item.status == InventoryStatus.DELIVERED:
        return False
    await db.delete(item)
    await db.commit()
    await cache.bump(NS_CATALOG)
    return True


async def inventory_stats(db: AsyncSession) -> dict[str, Any]:
    stmt = select(InventoryItem.status, func.count(InventoryItem.id)).group_by(
        InventoryItem.status
    )
    rows = (await db.execute(stmt)).all()
    counts = {str(status): int(count) for status, count in rows}
    low_stmt = (
        select(InventoryItem.product_id, func.count(InventoryItem.id).label("left_count"))
        .where(InventoryItem.status == InventoryStatus.AVAILABLE)
        .group_by(InventoryItem.product_id)
        .having(func.count(InventoryItem.id) <= 3)
    )
    low = [
        {"product_id": int(pid), "left": int(left)}
        for pid, left in (await db.execute(low_stmt)).all()
    ]
    return {
        "available": counts.get("available", 0),
        "reserved": counts.get("reserved", 0),
        "delivered": counts.get("delivered", 0),
        "low_stock": low,
    }
