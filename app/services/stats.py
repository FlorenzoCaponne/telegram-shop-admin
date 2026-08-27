"""Статистика для дашборда админки (ТЗ п.37)."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    InventoryItem,
    InventoryStatus,
    Order,
    OrderStatus,
    Payment,
    PaymentStatus,
    Product,
    User,
)

PAID_STATUSES = (OrderStatus.PAID, OrderStatus.PROCESSING, OrderStatus.COMPLETED)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


async def _scalar(db: AsyncSession, stmt: Any) -> Any:
    return (await db.execute(stmt)).scalar()


async def overview(db: AsyncSession) -> dict[str, Any]:
    """Карточки верхнего ряда: выручка, заказы, пользователи, остатки."""
    now = _now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week = now - dt.timedelta(days=7)
    month = now - dt.timedelta(days=30)

    revenue_total = await _scalar(
        db,
        select(func.coalesce(func.sum(Order.total), 0)).where(Order.status.in_(PAID_STATUSES)),
    )
    revenue_today = await _scalar(
        db,
        select(func.coalesce(func.sum(Order.total), 0)).where(
            Order.status.in_(PAID_STATUSES), Order.paid_at >= today
        ),
    )
    revenue_week = await _scalar(
        db,
        select(func.coalesce(func.sum(Order.total), 0)).where(
            Order.status.in_(PAID_STATUSES), Order.paid_at >= week
        ),
    )
    revenue_month = await _scalar(
        db,
        select(func.coalesce(func.sum(Order.total), 0)).where(
            Order.status.in_(PAID_STATUSES), Order.paid_at >= month
        ),
    )

    orders_total = await _scalar(db, select(func.count(Order.id)))
    orders_today = await _scalar(
        db, select(func.count(Order.id)).where(Order.created_at >= today)
    )
    orders_paid = await _scalar(
        db, select(func.count(Order.id)).where(Order.status.in_(PAID_STATUSES))
    )
    orders_pending = await _scalar(
        db,
        select(func.count(Order.id)).where(
            Order.status.in_([OrderStatus.CREATED, OrderStatus.PAYMENT_PENDING])
        ),
    )

    users_total = await _scalar(db, select(func.count(User.id)))
    users_today = await _scalar(
        db, select(func.count(User.id)).where(User.created_at >= today)
    )
    users_active_week = await _scalar(
        db, select(func.count(User.id)).where(User.last_seen_at >= week)
    )
    users_blocked = await _scalar(
        db, select(func.count(User.id)).where(User.is_blocked.is_(True))
    )

    inventory_available = await _scalar(
        db,
        select(func.count(InventoryItem.id)).where(
            InventoryItem.status == InventoryStatus.AVAILABLE
        ),
    )
    products_active = await _scalar(
        db, select(func.count(Product.id)).where(Product.is_active.is_(True))
    )

    total_orders = int(orders_total or 0)
    paid_orders = int(orders_paid or 0)
    conversion = (paid_orders / total_orders * 100) if total_orders else 0.0
    avg_check = (Decimal(revenue_total or 0) / paid_orders) if paid_orders else Decimal("0")

    return {
        "revenue_total": Decimal(revenue_total or 0),
        "revenue_today": Decimal(revenue_today or 0),
        "revenue_week": Decimal(revenue_week or 0),
        "revenue_month": Decimal(revenue_month or 0),
        "orders_total": total_orders,
        "orders_today": int(orders_today or 0),
        "orders_paid": paid_orders,
        "orders_pending": int(orders_pending or 0),
        "users_total": int(users_total or 0),
        "users_today": int(users_today or 0),
        "users_active_week": int(users_active_week or 0),
        "users_blocked": int(users_blocked or 0),
        "inventory_available": int(inventory_available or 0),
        "products_active": int(products_active or 0),
        "conversion": round(conversion, 1),
        "avg_check": avg_check.quantize(Decimal("0.01")) if paid_orders else Decimal("0.00"),
    }


async def revenue_series(db: AsyncSession, days: int = 14) -> list[dict[str, Any]]:
    """Выручка и число заказов по дням без дыр в датах."""
    days = max(1, min(90, int(days)))
    since = (_now() - dt.timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    stmt = (
        select(
            func.date(Order.paid_at).label("day"),
            func.count(Order.id),
            func.coalesce(func.sum(Order.total), 0),
        )
        .where(Order.status.in_(PAID_STATUSES), Order.paid_at >= since)
        .group_by(func.date(Order.paid_at))
    )
    rows = (await db.execute(stmt)).all()
    buckets: dict[str, dict[str, Any]] = {}
    for day, count, amount in rows:
        key = str(day)[:10]
        buckets[key] = {"orders": int(count), "revenue": Decimal(amount or 0)}

    series: list[dict[str, Any]] = []
    for index in range(days):
        day = (since + dt.timedelta(days=index)).date()
        key = day.isoformat()
        bucket = buckets.get(key, {"orders": 0, "revenue": Decimal("0")})
        series.append(
            {
                "date": key,
                "label": day.strftime("%d.%m"),
                "orders": bucket["orders"],
                "revenue": bucket["revenue"],
            }
        )
    return series


async def top_products(db: AsyncSession, *, days: int = 30, limit: int = 10) -> list[dict[str, Any]]:
    since = _now() - dt.timedelta(days=max(1, int(days)))
    stmt = (
        select(
            Order.product_id,
            Order.product_title,
            Order.product_emoji,
            func.count(Order.id),
            func.coalesce(func.sum(Order.total), 0),
        )
        .where(Order.status.in_(PAID_STATUSES), Order.paid_at >= since)
        .group_by(Order.product_id, Order.product_title, Order.product_emoji)
        .order_by(func.coalesce(func.sum(Order.total), 0).desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "product_id": int(product_id) if product_id else None,
            "title": title,
            "emoji": emoji,
            "sales": int(count),
            "revenue": Decimal(amount or 0),
        }
        for product_id, title, emoji, count, amount in rows
    ]


async def orders_by_status(db: AsyncSession) -> list[dict[str, Any]]:
    stmt = select(Order.status, func.count(Order.id)).group_by(Order.status)
    rows = (await db.execute(stmt)).all()
    return [{"status": str(status), "count": int(count)} for status, count in rows]


async def payments_by_method(db: AsyncSession, days: int = 30) -> list[dict[str, Any]]:
    since = _now() - dt.timedelta(days=max(1, int(days)))
    stmt = (
        select(
            Payment.method_code,
            func.count(Payment.id),
            func.coalesce(func.sum(Payment.amount), 0),
        )
        .where(Payment.status == PaymentStatus.CONFIRMED, Payment.created_at >= since)
        .group_by(Payment.method_code)
        .order_by(func.count(Payment.id).desc())
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "method_code": int(code) if code is not None else None,
            "count": int(count),
            "amount": Decimal(amount or 0),
        }
        for code, count, amount in rows
    ]


async def low_stock(db: AsyncSession, threshold: int = 3, limit: int = 10) -> list[dict[str, Any]]:
    stmt = (
        select(
            Product.id,
            Product.title,
            Product.emoji,
            func.count(InventoryItem.id).label("left_count"),
        )
        .join(
            InventoryItem,
            (InventoryItem.product_id == Product.id)
            & (InventoryItem.status == InventoryStatus.AVAILABLE),
            isouter=True,
        )
        .where(Product.is_active.is_(True))
        .group_by(Product.id, Product.title, Product.emoji)
        .having(func.count(InventoryItem.id) <= threshold)
        .order_by(func.count(InventoryItem.id))
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "product_id": int(product_id),
            "title": (title or {}).get("ru") or (title or {}).get("en") or "—",
            "emoji": emoji,
            "left": int(left),
        }
        for product_id, title, emoji, left in rows
    ]


async def recent_orders(db: AsyncSession, limit: int = 10) -> list[dict[str, Any]]:
    stmt = select(Order).order_by(Order.created_at.desc()).limit(limit)
    rows = (await db.execute(stmt)).scalars().unique().all()
    return [
        {
            "id": row.id,
            "public_no": row.public_no,
            "title": row.product_title,
            "emoji": row.product_emoji,
            "total": row.total,
            "currency": row.currency,
            "status": str(row.status),
            "created_at": row.created_at,
        }
        for row in rows
    ]


async def dashboard(db: AsyncSession) -> dict[str, Any]:
    return {
        "overview": await overview(db),
        "series": await revenue_series(db, days=14),
        "top_products": await top_products(db, days=30, limit=5),
        "statuses": await orders_by_status(db),
        "methods": await payments_by_method(db, days=30),
        "low_stock": await low_stock(db),
        "recent_orders": await recent_orders(db),
    }
