"""Базовые классы и общие типы для моделей."""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, MetaData, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Единая схема имён констрейнтов — критично для читаемых миграций Alembic.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# JSONB в PostgreSQL, JSON в SQLite (для быстрых юнит-тестов бизнес-логики).
JSONType = JSONB().with_variant(JSON(), "sqlite")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map = {dict[str, Any]: JSONType, list[Any]: JSONType}

    def __repr__(self) -> str:  # pragma: no cover - только для отладки
        pk = getattr(self, "id", None)
        return f"<{type(self).__name__} id={pk}>"


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class IntPK:
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)
