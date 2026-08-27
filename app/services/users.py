"""Пользователи бота (ТЗ п.26, п.27)."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any, Sequence

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Order, OrderStatus, User

log = structlog.get_logger(__name__)


async def get_or_create(
    db: AsyncSession,
    *,
    tg_id: int,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    language: str | None = None,
) -> User:
    """Найти или создать пользователя. Безопасно при параллельных апдейтах."""
    user = (await db.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    now = dt.datetime.now(dt.UTC)
    if user is not None:
        changed = False
        if username != user.username:
            user.username, changed = username, True
        if first_name and first_name != user.first_name:
            user.first_name, changed = first_name, True
        if last_name != user.last_name:
            user.last_name, changed = last_name, True
        if user.bot_blocked:
            user.bot_blocked, changed = False, True
        user.last_seen_at = now
        await db.commit()
        if changed:
            log.debug("user.updated", tg_id=tg_id)
        return user

    stmt = (
        pg_insert(User)
        .values(
            tg_id=tg_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            language=(language or "ru")[:8],
            last_seen_at=now,
        )
        .on_conflict_do_nothing(index_elements=[User.tg_id])
    )
    await db.execute(stmt)
    await db.commit()
    user = (await db.execute(select(User).where(User.tg_id == tg_id))).scalar_one()
    log.info("user.created", tg_id=tg_id, username=username)
    return user


async def touch(db: AsyncSession, user: User) -> None:
    user.last_seen_at = dt.datetime.now(dt.UTC)
    await db.commit()


async def set_language(db: AsyncSession, user: User, language: str) -> User:
    user.language = (language or "ru")[:8]
    await db.commit()
    return user


async def set_blocked(db: AsyncSession, user_id: int, blocked: bool) -> User | None:
    user = await db.get(User, user_id)
    if user is None:
        return None
    user.is_blocked = blocked
    await db.commit()
    return user


async def mark_bot_blocked(db: AsyncSession, tg_id: int, blocked: bool = True) -> None:
    """Пользователь заблокировал бота — больше не шлём ему рассылки."""
    user = (await db.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    if user is None:
        return
    user.bot_blocked = blocked
    await db.commit()


async def set_notes(db: AsyncSession, user_id: int, notes: str | None) -> User | None:
    user = await db.get(User, user_id)
    if user is None:
        return None
    user.notes = notes
    await db.commit()
    return user


async def get(db: AsyncSession, user_id: int) -> User | None:
    return await db.get(User, user_id)


async def get_by_tg_id(db: AsyncSession, tg_id: int) -> User | None:
    return (await db.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()


async def list_users(
    db: AsyncSession,
    *,
    query: str | None = None,
    language: str | None = None,
    only_buyers: bool = False,
    only_blocked: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> tuple[Sequence[User], int]:
    stmt = select(User)
    count_stmt = select(func.count(User.id))
    conditions = []

    if query:
        raw = query.strip()
        like = f"%{raw}%"
        parts = [User.username.ilike(like), User.first_name.ilike(like), User.last_name.ilike(like)]
        if raw.lstrip("-").isdigit():
            parts.append(User.tg_id == int(raw))
        conditions.append(or_(*parts))
    if language:
        conditions.append(User.language == language)
    if only_buyers:
        conditions.append(User.orders_count > 0)
    if only_blocked:
        conditions.append(User.is_blocked.is_(True))

    for condition in conditions:
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)

    stmt = stmt.order_by(User.id.desc()).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().all()
    total = int((await db.execute(count_stmt)).scalar() or 0)
    return rows, total


async def register_purchase(db: AsyncSession, user_id: int, amount: Decimal) -> None:
    """Счётчики покупок обновляются одним UPDATE — без гонок."""
    user = await db.get(User, user_id)
    if user is None:
        return
    user.orders_count = (user.orders_count or 0) + 1
    user.total_spent = (user.total_spent or Decimal("0")) + Decimal(amount)
    await db.commit()


async def user_orders(db: AsyncSession, user_id: int, limit: int = 20) -> Sequence[Order]:
    stmt = (
        select(Order)
        .where(Order.user_id == user_id)
        .order_by(Order.id.desc())
        .limit(limit)
    )
    return (await db.execute(stmt)).scalars().unique().all()


async def stats(db: AsyncSession, user_id: int) -> dict[str, Any]:
    completed = select(func.count(Order.id)).where(
        Order.user_id == user_id, Order.status == OrderStatus.COMPLETED
    )
    total = select(func.count(Order.id)).where(Order.user_id == user_id)
    spent = select(func.coalesce(func.sum(Order.total), 0)).where(
        Order.user_id == user_id,
        Order.status.in_([OrderStatus.PAID, OrderStatus.PROCESSING, OrderStatus.COMPLETED]),
    )
    return {
        "orders_total": int((await db.execute(total)).scalar() or 0),
        "orders_completed": int((await db.execute(completed)).scalar() or 0),
        "spent": (await db.execute(spent)).scalar() or Decimal("0"),
    }


async def counters(db: AsyncSession) -> dict[str, int]:
    """Сводка для дашборда."""
    day_ago = dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
    total = int((await db.execute(select(func.count(User.id)))).scalar() or 0)
    buyers = int(
        (await db.execute(select(func.count(User.id)).where(User.orders_count > 0))).scalar() or 0
    )
    new_today = int(
        (await db.execute(select(func.count(User.id)).where(User.created_at >= day_ago))).scalar()
        or 0
    )
    blocked = int(
        (await db.execute(select(func.count(User.id)).where(User.is_blocked.is_(True)))).scalar()
        or 0
    )
    return {"total": total, "buyers": buyers, "new_today": new_today, "blocked": blocked}
