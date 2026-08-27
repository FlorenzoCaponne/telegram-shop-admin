"""Миддлвары бота: сессия БД, пользователь, антифлуд, логирование.

Каждый апдейт получает свою AsyncSession (data["db"]) и уже прогретого
пользователя (data["user"], data["locale"]). Сервисы и хендлеры не создают
сессии сами.
"""
from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

import structlog
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update, User as TgUser

from app.core.cache import cache
from app.core.config import settings
from app.db.session import session_scope
from app.services import cms, users as users_service

log = structlog.get_logger(__name__)


def _tg_user(event: TelegramObject) -> TgUser | None:
    for attr in ("from_user", "message", "callback_query"):
        value = getattr(event, attr, None)
        if isinstance(value, TgUser):
            return value
        if value is not None and hasattr(value, "from_user"):
            return getattr(value, "from_user")
    return None


class DbSessionMiddleware(BaseMiddleware):
    """Открывает и гарантированно закрывает сессию БД на один апдейт."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with session_scope() as db:
            data["db"] = db
            return await handler(event, data)


class UserMiddleware(BaseMiddleware):
    """Создаёт/обновляет пользователя и отсекает заблокированных."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user") or _tg_user(event)
        db = data.get("db")
        if tg_user is None or db is None or tg_user.is_bot:
            return await handler(event, data)

        user = await users_service.get_or_create(
            db,
            tg_id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name,
            last_name=tg_user.last_name,
            language=(tg_user.language_code or settings.default_locale)[:2],
        )
        data["user"] = user
        data["locale"] = cms.normalize_locale(user.language)

        if user.is_blocked:
            text = await cms.t(db, "common.blocked", data["locale"])
            if isinstance(event, CallbackQuery):
                await event.answer(text[:180], show_alert=True)
            elif isinstance(event, Message):
                await event.answer(text)
            return None

        return await handler(event, data)


class ThrottlingMiddleware(BaseMiddleware):
    """Ограничение частоты действий: N событий в секунду на пользователя.

    Реализация fail-open: если Redis недоступен, бот продолжает работать.
    """

    def __init__(self, default_limit: int | None = None) -> None:
        self.default_limit = default_limit or settings.rate_limit_per_second

    async def _allowed(self, tg_id: int, limit: int) -> bool:
        second = int(time.time())
        for slot in range(max(1, limit)):
            if await cache.acquire_once(f"rl:{tg_id}:{second}:{slot}", ttl=2):
                return True
        return False

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user") or _tg_user(event)
        db = data.get("db")
        if tg_user is None:
            return await handler(event, data)

        limit = self.default_limit
        if db is not None:
            try:
                limit = int(
                    await cms.setting(db, "bot.rate_limit_per_second", limit) or limit
                )
            except Exception:  # pragma: no cover
                limit = self.default_limit

        if await self._allowed(tg_user.id, limit):
            return await handler(event, data)

        locale = data.get("locale") or settings.default_locale
        if isinstance(event, CallbackQuery):
            text = (
                await cms.t(db, "common.rate_limited", locale)
                if db is not None
                else "Слишком быстро — подождите секунду."
            )
            await event.answer(text[:180], show_alert=False)
        log.debug("bot.throttled", tg_id=tg_user.id, limit=limit)
        return None


class LoggingMiddleware(BaseMiddleware):
    """Структурное логирование апдейтов и времени обработки."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        started = time.perf_counter()
        kind = type(event).__name__
        payload = None
        if isinstance(event, CallbackQuery):
            payload = event.data
        elif isinstance(event, Message):
            payload = (event.text or "")[:64]
        try:
            return await handler(event, data)
        except Exception as exc:
            log.exception(
                "bot.handler_failed", kind=kind, payload=payload, error=str(exc)
            )
            raise
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            if elapsed > 500:
                log.warning("bot.slow_update", kind=kind, payload=payload, ms=round(elapsed))


def setup(dispatcher: Any) -> None:
    """Порядок важен: лог → сессия → пользователь → антифлуд."""
    for observer in (dispatcher.message, dispatcher.callback_query):
        observer.middleware(LoggingMiddleware())
        observer.middleware(DbSessionMiddleware())
        observer.middleware(UserMiddleware())
        observer.middleware(ThrottlingMiddleware())
