"""Аудит деи́ствии́ админов (ТЗ п.38)."""
from __future__ import annotations

import datetime as dt
from typing import Any, Sequence

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog

log = structlog.get_logger(__name__)

MAX_SUMMARY = 2000


def _clean(value: Any) -> dict[str, Any]:
    """Скрываем секреты и приводим к JSON-сериализуемому виду."""
    if not isinstance(value, dict):
        return {} if value is None else {"value": str(value)[:500]}
    secret_markers = ("secret", "password", "token", "api_key", "apikey")
    result: dict[str, Any] = {}
    for key, item in value.items():
        lowered = str(key).lower()
        if any(marker in lowered for marker in secret_markers):
            result[key] = "***"
        elif isinstance(item, (str, int, float, bool)) or item is None:
            result[key] = item if not isinstance(item, str) else item[:500]
        else:
            result[key] = str(item)[:500]
    return result


async def record(
    db: AsyncSession,
    *,
    admin_id: int | None = None,
    admin_login: str | None = None,
    action: str,
    entity: str,
    entity_id: str | int | None = None,
    summary: str | None = None,
    old_value: Any = None,
    new_value: Any = None,
    ip: str | None = None,
    user_agent: str | None = None,
    commit: bool = True,
) -> AuditLog:
    """Записать событие в журнал."""
    entry = AuditLog(
        admin_id=admin_id,
        admin_login=admin_login,
        action=action[:64],
        entity=entity[:64],
        entity_id=str(entity_id)[:64] if entity_id is not None else None,
        summary=(summary or "")[:MAX_SUMMARY] or None,
        old_value=_clean(old_value),
        new_value=_clean(new_value),
        ip=(ip or "")[:64] or None,
        user_agent=(user_agent or "")[:255] or None,
    )
    db.add(entry)
    if commit:
        await db.commit()
    else:
        await db.flush()
    log.info(
        "audit",
        admin=admin_login,
        action=action,
        entity=entity,
        entity_id=str(entity_id) if entity_id is not None else None,
    )
    return entry


async def list_logs(
    db: AsyncSession,
    *,
    entity: str | None = None,
    action: str | None = None,
    admin_id: int | None = None,
    query: str | None = None,
    date_from: dt.datetime | None = None,
    date_to: dt.datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[Sequence[AuditLog], int]:
    """Страница журнала + общее количество записей."""
    stmt = select(AuditLog)
    count_stmt = select(func.count(AuditLog.id))

    conditions = []
    if entity:
        conditions.append(AuditLog.entity == entity)
    if action:
        conditions.append(AuditLog.action == action)
    if admin_id:
        conditions.append(AuditLog.admin_id == admin_id)
    if query:
        like = f"%{query.strip()}%"
        conditions.append(AuditLog.summary.ilike(like))
    if date_from:
        conditions.append(AuditLog.created_at >= date_from)
    if date_to:
        conditions.append(AuditLog.created_at <= date_to)

    for condition in conditions:
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)

    stmt = stmt.order_by(AuditLog.id.desc()).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().all()
    total = int((await db.execute(count_stmt)).scalar() or 0)
    return rows, total


async def distinct_entities(db: AsyncSession) -> list[str]:
    rows = (await db.execute(select(AuditLog.entity).distinct().order_by(AuditLog.entity))).scalars()
    return [row for row in rows if row]


async def distinct_actions(db: AsyncSession) -> list[str]:
    rows = (await db.execute(select(AuditLog.action).distinct().order_by(AuditLog.action))).scalars()
    return [row for row in rows if row]
