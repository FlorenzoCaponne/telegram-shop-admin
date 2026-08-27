"""Единая точка отправки сообщений в Telegram.

Не импортирует app.bot.bot — экземпляр Bot регистрируется через set_bot(),
чтобы не было циклических импортов с хендлерами и сервисами.
"""
from __future__ import annotations

import asyncio
from typing import Any, Sequence

import structlog
from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.core.config import settings

log = structlog.get_logger(__name__)

_bot: Bot | None = None


def set_bot(bot: Bot | None) -> None:
    global _bot
    _bot = bot


def get_bot() -> Bot | None:
    return _bot


def require_bot() -> Bot:
    if _bot is None:
        raise RuntimeError("Bot is not initialized: check BOT_TOKEN")
    return _bot


def build_markup(buttons: Sequence[dict[str, Any]] | None) -> InlineKeyboardMarkup | None:
    """Собрать клавиатуру из списка словарей (формат админки и рассылок).

    Ожидается: [{"text": "...", "url": "..."}] или {"text": "...", "callback": "..."},
    опционально "row": <int> для группировки.
    """
    if not buttons:
        return None
    rows: dict[int, list[InlineKeyboardButton]] = {}
    for index, item in enumerate(buttons):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("title") or "").strip()
        if not text:
            continue
        url = str(item.get("url") or "").strip()
        callback_data = str(item.get("callback") or item.get("callback_data") or "").strip()
        if url:
            button = InlineKeyboardButton(text=text, url=url)
        elif callback_data:
            button = InlineKeyboardButton(text=text, callback_data=callback_data[:64])
        else:
            continue
        try:
            row = int(item.get("row", index))
        except (TypeError, ValueError):
            row = index
        rows.setdefault(row, []).append(button)

    keyboard = [rows[key] for key in sorted(rows) if rows[key]]
    if not keyboard:
        return None
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


async def _call(coro_factory, *, attempts: int = 3):
    """Вызов Telegram API с учётом flood-лимитов."""
    for attempt in range(1, attempts + 1):
        try:
            return await coro_factory()
        except TelegramRetryAfter as exc:
            delay = float(getattr(exc, "retry_after", 1) or 1)
            log.warning("tg.flood_wait", delay=delay, attempt=attempt)
            await asyncio.sleep(delay + 0.2)
        except TelegramForbiddenError:
            raise
        except TelegramBadRequest:
            raise
    raise TelegramRetryAfter(
        method=None, message="retry limit exceeded", retry_after=1
    ) if False else RuntimeError("telegram retry limit exceeded")


async def send_message(
    tg_id: int,
    text: str,
    *,
    markup: InlineKeyboardMarkup | None = None,
    image_url: str | None = None,
    parse_mode: str | None = "HTML",
    disable_preview: bool = True,
) -> Any:
    """Отправить сообщение; при наличии картинки — фото с подписью."""
    bot = require_bot()
    if image_url:
        try:
            return await _call(
                lambda: bot.send_photo(
                    chat_id=tg_id,
                    photo=image_url,
                    caption=text[:1024],
                    parse_mode=parse_mode,
                    reply_markup=markup,
                )
            )
        except TelegramBadRequest as exc:
            # Картинка недоступна — падаем на текстовое сообщение.
            log.warning("tg.photo_failed", tg_id=tg_id, error=str(exc))
    return await _call(
        lambda: bot.send_message(
            chat_id=tg_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=markup,
            disable_web_page_preview=disable_preview,
        )
    )


async def edit_message(
    *,
    chat_id: int,
    message_id: int,
    text: str,
    markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = "HTML",
    has_media: bool = False,
) -> bool:
    """Отредактировать сообщение. False = нужно отправить новое."""
    bot = require_bot()
    try:
        if has_media:
            await bot.edit_message_caption(
                chat_id=chat_id,
                message_id=message_id,
                caption=text[:1024],
                parse_mode=parse_mode,
                reply_markup=markup,
            )
        else:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=markup,
                disable_web_page_preview=True,
            )
        return True
    except TelegramBadRequest as exc:
        message = str(exc).lower()
        if "message is not modified" in message:
            return True
        log.debug("tg.edit_failed", chat_id=chat_id, error=str(exc))
        return False


async def delete_message(chat_id: int, message_id: int) -> None:
    bot = get_bot()
    if bot is None:
        return
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:  # сообщение уже удалено или слишком старое
        pass


async def send_broadcast_message(
    *,
    tg_id: int,
    text: str,
    image_url: str | None = None,
    buttons: Sequence[dict[str, Any]] | None = None,
    locale: str | None = None,
) -> str:
    """Результат: "ok" | "blocked" | "failed"."""
    if get_bot() is None:
        return "failed"
    try:
        await send_message(
            tg_id,
            text,
            markup=build_markup(buttons),
            image_url=image_url,
        )
        return "ok"
    except TelegramForbiddenError:
        return "blocked"
    except Exception as exc:
        log.warning("broadcast.send_failed", tg_id=tg_id, error=str(exc))
        return "failed"


async def notify_admins(text: str, *, markup: InlineKeyboardMarkup | None = None) -> None:
    """Уведомить админов из ADMIN_IDS (заказы, ошибки выдачи)."""
    if get_bot() is None:
        return
    for admin_id in settings.admin_id_list:
        try:
            await send_message(admin_id, text, markup=markup)
        except Exception as exc:
            log.debug("notify_admin_failed", admin_id=admin_id, error=str(exc))


async def answer_callback(callback: Any, text: str | None = None, *, alert: bool = False) -> None:
    """Мгновенный ответ на callback — чтобы в боте не крутился спиннер."""
    try:
        await callback.answer(text=text, show_alert=alert)
    except Exception:
        pass
