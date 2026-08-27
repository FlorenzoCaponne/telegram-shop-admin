"""Показ экранов: единая логика «отредактировать или отправить заново».

Текстовое сообщение нельзя превратить в фото через edit, поэтому при смене
типа сообщения старое удаляется (если так настроено в админке).
"""
from __future__ import annotations

from typing import Any

from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import sender
from app.bot.renderer import Screen
from app.services import cms


async def show(
    event: Message | CallbackQuery,
    screen: Screen,
    *,
    db: AsyncSession | None = None,
) -> None:
    """Показать экран в ответ на сообщение или нажатие кнопки."""
    parse_mode = "HTML"
    if db is not None:
        parse_mode = str(await cms.design_value(db, "parse_mode", "HTML") or "HTML")

    if isinstance(event, CallbackQuery):
        message = event.message
        if message is None:
            await sender.send_message(
                event.from_user.id,
                screen.text,
                markup=screen.markup,
                image_url=screen.image_url,
                parse_mode=parse_mode,
            )
            return

        had_media = bool(getattr(message, "photo", None))
        wants_media = bool(screen.image_url)

        if had_media == wants_media:
            edited = await sender.edit_message(
                chat_id=message.chat.id,
                message_id=message.message_id,
                text=screen.text,
                markup=screen.markup,
                parse_mode=parse_mode,
                has_media=had_media,
            )
            if edited:
                return

        delete_previous = True
        if db is not None:
            delete_previous = bool(
                await cms.design_value(db, "delete_previous_message", True)
            )
        if delete_previous:
            await sender.delete_message(message.chat.id, message.message_id)

        await sender.send_message(
            message.chat.id,
            screen.text,
            markup=screen.markup,
            image_url=screen.image_url,
            parse_mode=parse_mode,
        )
        return

    await sender.send_message(
        event.chat.id,
        screen.text,
        markup=screen.markup,
        image_url=screen.image_url,
        parse_mode=parse_mode,
    )


async def toast(
    event: Message | CallbackQuery, text: str, *, alert: bool = False
) -> None:
    """Короткое уведомление без смены экрана."""
    if isinstance(event, CallbackQuery):
        await sender.answer_callback(event, text[:180], alert=alert)
    else:
        await event.answer(text)
