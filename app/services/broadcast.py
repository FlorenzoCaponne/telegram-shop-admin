"""Рассылки по аудитории (ТЗ п.27, п.36).

Отправка идёт пачками с курсором по user_id: рассылку можно паузить,
продолжить и она сама восстанавливается после рестарта приложения.
Скорость ограничена настройкой bot.broadcast_rate (сообщений/сек).
"""
from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any, Sequence

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import session_scope
from app.models import Broadcast, BroadcastStatus, Order, OrderStatus, User
from app.services import cms

log = structlog.get_logger(__name__)

BATCH_SIZE = 100
SEGMENTS = ("all", "buyers", "non_buyers", "active", "blocked")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# =====================================================================
#  АУДИТОРИЯ
# =====================================================================
def audience_conditions(audience: dict[str, Any]) -> list[Any]:
    """Условия выборки пользователей для рассылки."""
    segment = str((audience or {}).get("segment") or "all")
    language = str((audience or {}).get("language") or "").strip()

    conditions: list[Any] = [User.bot_blocked.is_(False)]
    if segment != "blocked":
        conditions.append(User.is_blocked.is_(False))

    paid_orders = (
        select(Order.user_id)
        .where(
            Order.status.in_(
                [OrderStatus.PAID, OrderStatus.PROCESSING, OrderStatus.COMPLETED]
            )
        )
        .scalar_subquery()
    )

    if segment == "buyers":
        conditions.append(User.id.in_(paid_orders))
    elif segment == "non_buyers":
        conditions.append(User.id.not_in(paid_orders))
    elif segment == "active":
        conditions.append(User.last_seen_at >= _now() - dt.timedelta(days=30))
    elif segment == "blocked":
        conditions = [User.is_blocked.is_(True)]

    if language:
        conditions.append(User.language == language)
    return conditions


def audience_query(audience: dict[str, Any]) -> Any:
    stmt = select(User)
    for condition in audience_conditions(audience):
        stmt = stmt.where(condition)
    return stmt.order_by(User.id)


async def audience_size(db: AsyncSession, audience: dict[str, Any]) -> int:
    stmt = select(func.count(User.id))
    for condition in audience_conditions(audience):
        stmt = stmt.where(condition)
    return int((await db.execute(stmt)).scalar() or 0)


# =====================================================================
#  CRUD И УПРАВЛЕНИЕ
# =====================================================================
async def list_broadcasts(
    db: AsyncSession, *, limit: int = 50, offset: int = 0
) -> tuple[Sequence[Broadcast], int]:
    stmt = select(Broadcast).order_by(Broadcast.id.desc()).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().unique().all()
    total = int((await db.execute(select(func.count(Broadcast.id)))).scalar() or 0)
    return rows, total


async def get_broadcast(db: AsyncSession, broadcast_id: int) -> Broadcast | None:
    return await db.get(Broadcast, broadcast_id)


async def save_broadcast(
    db: AsyncSession,
    *,
    broadcast_id: int | None,
    data: dict[str, Any],
    admin_id: int | None = None,
) -> Broadcast:
    item = await db.get(Broadcast, broadcast_id) if broadcast_id else None
    if item is None:
        item = Broadcast(name="", text="", created_by=admin_id)
        db.add(item)

    if item.status == BroadcastStatus.RUNNING:
        # На ходу менять текст нельзя — сначала пауза.
        return item

    item.name = str(data.get("name") or "Без названия")[:255]
    item.text = str(data.get("text") or "")
    item.image_url = data.get("image_url") or None
    item.buttons = data.get("buttons") or []
    item.audience = {
        "segment": str(data.get("segment") or "all"),
        "language": str(data.get("language") or ""),
    }
    await db.commit()
    await db.refresh(item)
    return item


async def delete_broadcast(db: AsyncSession, broadcast_id: int) -> bool:
    item = await db.get(Broadcast, broadcast_id)
    if item is None or item.status == BroadcastStatus.RUNNING:
        return False
    await db.delete(item)
    await db.commit()
    return True


async def start(db: AsyncSession, broadcast_id: int) -> Broadcast | None:
    item = await db.get(Broadcast, broadcast_id)
    if item is None or item.status in {BroadcastStatus.RUNNING, BroadcastStatus.DONE}:
        return item
    item.status = BroadcastStatus.RUNNING
    item.started_at = item.started_at or _now()
    item.finished_at = None
    item.total = await audience_size(db, item.audience or {})
    await db.commit()
    asyncio.create_task(run(item.id))
    log.info("broadcast.started", broadcast_id=item.id, total=item.total)
    return item


async def pause(db: AsyncSession, broadcast_id: int) -> Broadcast | None:
    item = await db.get(Broadcast, broadcast_id)
    if item is None or item.status != BroadcastStatus.RUNNING:
        return item
    item.status = BroadcastStatus.PAUSED
    await db.commit()
    return item


async def cancel(db: AsyncSession, broadcast_id: int) -> Broadcast | None:
    item = await db.get(Broadcast, broadcast_id)
    if item is None or item.status == BroadcastStatus.DONE:
        return item
    item.status = BroadcastStatus.CANCELLED
    item.finished_at = _now()
    await db.commit()
    return item


# =====================================================================
#  ОТПРАВКА
# =====================================================================
async def process_batch(db: AsyncSession, broadcast: Broadcast) -> int:
    """Отправить одну пачку. Возвращает число обработанных пользователей."""
    # Ленивый импорт: сервисный слой не должен зависеть от бота на импорте.
    from app.bot.sender import send_broadcast_message

    stmt = audience_query(broadcast.audience or {}).where(
        User.id > int(broadcast.cursor_user_id or 0)
    ).limit(BATCH_SIZE)
    users = (await db.execute(stmt)).scalars().unique().all()
    if not users:
        return 0

    rate = int(await cms.setting(db, "bot.broadcast_rate", 20) or 20)
    delay = 1.0 / max(1, rate)

    sent = failed = blocked = 0
    for user in users:
        outcome = await send_broadcast_message(
            tg_id=user.tg_id,
            text=broadcast.text,
            image_url=broadcast.image_url,
            buttons=broadcast.buttons or [],
            locale=user.language,
        )
        if outcome == "ok":
            sent += 1
        elif outcome == "blocked":
            blocked += 1
            user.bot_blocked = True
        else:
            failed += 1
        broadcast.cursor_user_id = user.id
        await asyncio.sleep(delay)

    broadcast.sent = int(broadcast.sent or 0) + sent
    broadcast.failed = int(broadcast.failed or 0) + failed
    broadcast.blocked = int(broadcast.blocked or 0) + blocked
    await db.commit()
    return len(users)


async def run(broadcast_id: int) -> None:
    """Фоновая задача рассылки со своей сессией БД."""
    while True:
        async with session_scope() as db:
            broadcast = await db.get(Broadcast, broadcast_id)
            if broadcast is None or broadcast.status != BroadcastStatus.RUNNING:
                return
            try:
                processed = await process_batch(db, broadcast)
            except Exception as exc:  # pragma: no cover - сетевые сбои
                log.exception("broadcast.batch_failed", broadcast_id=broadcast_id, error=str(exc))
                broadcast.status = BroadcastStatus.PAUSED
                await db.commit()
                return
            if processed == 0:
                broadcast.status = BroadcastStatus.DONE
                broadcast.finished_at = _now()
                await db.commit()
                log.info(
                    "broadcast.done",
                    broadcast_id=broadcast_id,
                    sent=broadcast.sent,
                    failed=broadcast.failed,
                    blocked=broadcast.blocked,
                )
                return
        await asyncio.sleep(0.2)


async def resume_unfinished() -> None:
    """После рестарта приложения продолжить прерванные рассылки."""
    async with session_scope() as db:
        stmt = select(Broadcast.id).where(Broadcast.status == BroadcastStatus.RUNNING)
        ids = [int(row) for row in (await db.execute(stmt)).scalars().all()]
    for broadcast_id in ids:
        log.info("broadcast.resumed", broadcast_id=broadcast_id)
        asyncio.create_task(run(broadcast_id))


async def preview(db: AsyncSession, data: dict[str, Any]) -> dict[str, Any]:
    """Предпросмотр для HTMX-блока «размер аудитории»."""
    audience = {
        "segment": str(data.get("segment") or "all"),
        "language": str(data.get("language") or ""),
    }
    return {"audience": audience, "count": await audience_size(db, audience)}
