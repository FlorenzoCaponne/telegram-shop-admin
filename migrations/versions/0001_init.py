"""Начальная схема БД (все таблицы магазина и CMS).

Revision ID: 0001
Revises: None

Почему схема создаётся из метаданных моделей, а не списком op.create_table:
это гарантирует, что начальная схема бит-в-бит совпадает с models.py
(19 таблиц, включая CHECK-ограничения, UNIQUE и составные индексы) и
исключает рассинхронизацию из-за ручного переноса полей.

Все ПОСЛЕДУЮЩИЕ изменения схемы делаются обычными миграциями:
    alembic revision --autogenerate -m "описание"
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

from app.db.base import Base

# Импорт моделей регистрирует таблицы в Base.metadata
import app.models  # noqa: F401

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind, checkfirst=True)
