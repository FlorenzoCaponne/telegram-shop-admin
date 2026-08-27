"""Статистика для дашборда админки (ТЗ п.30).

Все запросы — агрегаты на стороне БД, без выгрузки строк в Python.
Результаты кэшируются на 60 секунд — дашборд часто обновляется через HTMX.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Broadcast,
    BroadcastStatus,
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
LOW_STOCK_THRESHOLD = 3


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _day_start(offset_days: int = 0) -> dt.datetime:
    today = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    return today - dt.timedelta(days=offset_days)


async def _sum_revenue(db: AsyncSession, since: dt.datetime | None = None) -> Decimal:
    stmt = select(func.coalesce(func.sum(Order.total), 0)).where(
        Order.status.in_(PAID_STATUSES)
    )
    if since is not None:
        stmt = stmt.where(Order.paid_at >= since)
    return Decimal((await db.execute(stmt)).scalar() or 0)


async def _count_orders(
    db: AsyncSession,
    *,
    since: dt.datetime | None = None,
    statuses: tuple[OrderStatus, ...] | None = None,
) -> int:
    stmt = select(func.count(Order.id))
    if statuses:
        stmt = stmt.where(Order.status.in_(statuses))
    if since is not None:
        stmt = stmt.where(Order.created_at >= since)
    return int((await db.execute(stmt)).scalar() or 0)


async def overview(db: AsyncSession) -> dict[str, Any]:
    """Карточки верхнего ряда дашборда."""
    today = _day_start()
    yesterday = _day_start(1)
    week = _day_start(7)
    month = _day_start(30)

    revenue_today = await _sum_revenue(db, today)
    revenue_yesterday = Decimal(
        (
            await db.execute(
                select(func.coalesce(func.sum(Order.total), 0)).where(
                    Order.status.in_(PAID_STATUSES),
                    Order.paid_at >= yesterday,
                    Order.paid_at < today,
                )
            )
        ).scalar()
        or 0
    )

    users_total = int((await db.execute(select(func.count(User.id)))).scalar() or 0)
    users_today = int(
        (
            await db.execute(
                select(func.count(User.id)).where(User.created_at >= today)
            )
        ).scalar()
        or 0
    )
    users_active = int(
        (
            await db.execute(
                select(func.count(User.id)).where(User.last_seen_at >= week)
            )
        ).scalar()
        or 0
    )

    orders_total = await _count_orders(db)
    orders_paid = await _count_orders(db, statuses=PAID_STATUSES)
    orders_today = await _count_orders(db, since=today)
    orders_pending = await _count_orders(
        db, statuses=(OrderStatus.CREATED, OrderStatus.PAYMENT_PENDING)
    )

    revenue_total = await _sum_revenue(db)
    average = (revenue_total / orders_paid) if orders_paid else Decimal("0")

    delta = 0.0
    if revenue_yesterday:
        delta = float((revenue_today - revenue_yesterday) / revenue_yesterday * 100)

    return {
        "revenue_today": revenue_today,
        "revenue_yesterday": revenue_yesterday,
        "revenue_week": await _sum_revenue(db, week),
        "revenue_month": await _sum_revenue(db, month),
        "revenue_total": revenue_total,
        "revenue_delta": round(delta, 1),
        "average_order": average.quantize(Decimal("0.01")),
        "users_total": users_total,
        "users_today": users_today,
        "users_active": users_active,
        "orders_total": orders_total,
        "orders_paid": orders_paid,
        "orders_today": orders_today,
        "orders_pending": orders_pending,
        "conversion": round(orders_paid / orders_total * 100, 1) if orders_total else 0.0,
    }


async def revenue_chart(db: AsyncSession, days: int = 14) -> list[dict[str, Any]]:
    """Выручка и число заказов по дням без пробелов в датах."""
    days = max(1, min(int(days), 90))
    since = _day_start(days - 1)
    stmt = (
        select(
            func.date(Order.paid_at).label("day"),
            func.coalesce(func.sum(Order.total), 0),
            func.count(Order.id),
        )
        .where(Order.status.in_(PAID_STATUSES), Order.paid_at >= since)
        .group_by(func.date(Order.paid_at))
    )
    rows = {str(day): (Decimal(amount or 0), int(count)) for day, amount, count in (await db.execute(stmt)).all()}

    series: list[dict[str, Any]] = []
    for index in range(days):
        day = (since + dt.timedelta(days=index)).date()
        amount, count = rows.get(str(day), (Decimal("0"), 0))
        series.append(
            {
                "date": day.isoformat(),
                "label": day.strftime("%d.%m"),
                "amount": amount,
                "orders": count,
            }
        )
    return series


async def top_products(db: AsyncSession, *, days: int = 30, limit: int = 10) -> list[dict[str, Any]]:
    since = _day_start(max(1, int(days)))
    stmt = (
        select(
            Order.product_id,
            func.max(Order.product_title),
            func.count(Order.id),
            func.coalesce(func.sum(Order.total), 0),
        )
        .where(Order.status.in_(PAID_STATUSES), Order.paid_at >= since)
        .group_by(Order.product_id)
        .order_by(func.coalesce(func.sum(Order.total), 0).desc())
        .limit(limit)
    )
    return [
        {
            "product_id": product_id,
            "title": title or "—",
            "orders": int(count),
            "amount": Decimal(amount or 0),
        }
        for product_id, title, count, amount in (await db.execute(stmt)).all()
    ]


async def orders_by_status(db: AsyncSession) -> dict[str, int]:
    stmt = select(Order.status, func.count(Order.id)).group_by(Order.status)
    rows = (await db.execute(stmt)).all()
    result = {status.value: 0 for status in OrderStatus}
    for status, count in rows:
        key = status.value if hasattr(status, "value") else str(status)
        result[key] = int(count)
    return result


async def payments_by_status(db: AsyncSession) -> dict[str, int]:
    stmt = select(Payment.status, func.count(Payment.id)).group_by(Payment.status)
    rows = (await db.execute(stmt)).all()
    result = {status.value: 0 for status in PaymentStatus}
    for status, count in rows:
        key = status.value if hasattr(status, "value") else str(status)
        result[key] = int(count)
    return result


async def alerts(db: AsyncSession) -> list[dict[str, str]]:
    """Предупреждения для админа: закончившиеся ключи, зависшие заказы."""
    items: list[dict[str, str]] = []

    stock_stmt = (
        select(Product.id, Product.title, func.count(InventoryItem.id))
        .join(
            InventoryItem,
            (InventoryItem.product_id == Product.id)
            & (InventoryItem.status == InventoryStatus.AVAILABLE),
            isouter=True,
        )
        .where(Product.is_active.is_(True), Product.delivery_type == "inventory")
        .group_by(Product.id, Product.title)
        .having(func.count(InventoryItem.id) <= LOW_STOCK_THRESHOLD)
        .limit(10)
    )
    for product_id, title, count in (await db.execute(stock_stmt)).all():
        name = title.get("ru") if isinstance(title, dict) else str(title)
        items.append(
            {
                "level": "error" if int(count) == 0 else "warning",
                "text": f"Товар «{name}»: осталось {int(count)} позиций на складе",
                "url": f"inventory?product_id={product_id}",
            }
        )

    stuck = int(
        (
            await db.execute(
                select(func.count(Order.id)).where(
                    Order.status == OrderStatus.PAID,
                    Order.paid_at < _now() - dt.timedelta(minutes=10),
                )
            )
        ).scalar()
        or 0
    )
    if stuck:
        items.append(
            {
                "level": "error",
                "text": f"Зависло без выдачи заказов: {stuck}",
                "url": "orders?status=paid",
            }
        )

    manual = int(
        (
            await db.execute(
                select(func.count(Order.id)).where(Order.status == OrderStatus.PROCESSING)
            )
        ).scalar()
        or 0
    )
    if manual:
        items.append(
            {
                "level": "warning",
                "text": f"Заказов ждут ручной выдачи: {manual}",
                "url": "orders?status=processing",
            }
        )

    running = int(
        (
            await db.execute(
                select(func.count(Broadcast.id)).where(
                    Broadcast.status == BroadcastStatus.RUNNING
                )
            )
        ).scalar()
        or 0
    )
    if running:
        items.append(
            {
                "level": "info",
                "text": f"Активных рассылок: {running}",
                "url": "broadcasts",
            }
        )
    return items


async def recent_orders(db: AsyncSession, limit: int = 10) -> list[Order]:
    stmt = select(Order).order_by(Order.id.desc()).limit(limit)
    return list((await db.execute(stmt)).scalars().unique().all())


async def users_chart(db: AsyncSession, days: int = 14) -> list[dict[str, Any]]:
    days = max(1, min(int(days), 90))
    since = _day_start(days - 1)
    stmt = (
        select(func.date(User.created_at), func.count(User.id))
        .where(User.created_at >= since)
        .group_by(func.date(User.created_at))
    )
    rows = {str(day): int(count) for day, count in (await db.execute(stmt)).all()}
    series = []
    for index in range(days):
        day = (since + dt.timedelta(days=index)).date()
        series.append(
            {
                "date": day.isoformat(),
                "label": day.strftime("%d.%m"),
                "users": rows.get(str(day), 0),
            }
        )
    return series


async def funnel(db: AsyncSession, days: int = 30) -> dict[str, int]:
    """Воронка: создали заказ → пошли платить → оплатили → выдано."""
    since = _day_start(max(1, int(days)))
    stmt = select(
        func.count(Order.id),
        func.count(case((Order.status != OrderStatus.CREATED, 1))),
        func.count(case((Order.status.in_(PAID_STATUSES), 1))),
        func.count(case((Order.status == OrderStatus.COMPLETED, 1))),
    ).where(Order.created_at >= since)
    created, started, paid, completed = (await db.execute(stmt)).one()
    return {
        "created": int(created or 0),
        "payment_started": int(started or 0),
        "paid": int(paid or 0),
        "completed": int(completed or 0),
    }
