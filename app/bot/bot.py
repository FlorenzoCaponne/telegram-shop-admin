"""Инициализация aiogram: polling локально, webhook на VPS (ТЗ п.11, п.44)."""
from __future__ import annotations

import asyncio
from typing import Any

import structlog
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, Update

from app.bot import sender
from app.bot.handlers import get_router
from app.bot.middlewares import setup as setup_middlewares
from app.core.config import settings

log = structlog.get_logger(__name__)

_dispatcher: Dispatcher | None = None
_polling_task: asyncio.Task[Any] | None = None

COMMANDS = [
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="catalog", description="Каталог"),
    BotCommand(command="orders", description="Мои заказы"),
    BotCommand(command="promo", description="Промокод"),
    BotCommand(command="language", description="Язык / Language"),
    BotCommand(command="help", description="Помощь"),
]


def _build_storage():
    """Redis-хранилище FSM с автофоллбэком на память."""
    try:
        from aiogram.fsm.storage.redis import RedisStorage

        return RedisStorage.from_url(settings.redis_url)
    except Exception as exc:  # pragma: no cover
        log.warning("bot.fsm_memory_storage", error=str(exc))
        return MemoryStorage()


def create_bot() -> Bot | None:
    if not settings.bot_token:
        log.warning("bot.token_missing")
        return None
    return Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher(storage=_build_storage())
    setup_middlewares(dispatcher)
    dispatcher.include_router(get_router())
    return dispatcher


def get_dispatcher() -> Dispatcher | None:
    return _dispatcher


async def start_bot() -> None:
    """Запуск бота в выбранном режиме. Не блокирует старт FastAPI."""
    global _dispatcher, _polling_task

    bot = create_bot()
    if bot is None:
        return
    sender.set_bot(bot)
    _dispatcher = create_dispatcher()

    try:
        await bot.set_my_commands(COMMANDS)
    except Exception as exc:  # pragma: no cover
        log.warning("bot.set_commands_failed", error=str(exc))

    if settings.bot_mode == "webhook":
        await bot.delete_webhook(drop_pending_updates=False)
        await bot.set_webhook(
            url=settings.telegram_webhook_url,
            secret_token=settings.bot_webhook_secret or None,
            drop_pending_updates=False,
            allowed_updates=["message", "callback_query"],
        )
        log.info("bot.webhook_set", url=settings.telegram_webhook_url)
        return

    await bot.delete_webhook(drop_pending_updates=True)
    _polling_task = asyncio.create_task(
        _dispatcher.start_polling(
            bot, allowed_updates=["message", "callback_query"], handle_signals=False
        )
    )
    log.info("bot.polling_started")


async def stop_bot() -> None:
    global _dispatcher, _polling_task

    if _polling_task is not None:
        _polling_task.cancel()
        try:
            await _polling_task
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # pragma: no cover
            log.debug("bot.polling_stop_error", error=str(exc))
        _polling_task = None

    if _dispatcher is not None:
        try:
            await _dispatcher.storage.close()
        except Exception:  # pragma: no cover
            pass
        _dispatcher = None

    bot = sender.get_bot()
    if bot is not None:
        try:
            await bot.session.close()
        except Exception:  # pragma: no cover
            pass
        sender.set_bot(None)
    log.info("bot.stopped")


async def feed_webhook_update(payload: dict[str, Any]) -> None:
    """Обработать апдейт из HTTP-вебхука Telegram."""
    bot = sender.get_bot()
    if bot is None or _dispatcher is None:
        log.warning("bot.webhook_without_bot")
        return
    update = Update.model_validate(payload, context={"bot": bot})
    await _dispatcher.feed_update(bot, update)
