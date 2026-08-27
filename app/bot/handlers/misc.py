"""Промокоды, поддержка, инфо-страницы и catch-all (ТЗ п.25, п.29).

Роутер подключается последним, поэтому его fallback не перехватывает другие экраны.
"""
from __future__ import annotations

import structlog
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import callbacks as cb
from app.bot import renderer, sender, ui
from app.core.config import settings
from app.models import User
from app.services import cms, promo as promo_service

log = structlog.get_logger(__name__)
router = Router(name="misc")


class PromoStates(StatesGroup):
    waiting_code = State()


@router.callback_query(F.data == cb.pack(cb.PROMO))
async def cb_promo(
    callback: CallbackQuery, db: AsyncSession, locale: str, state: FSMContext
) -> None:
    await sender.answer_callback(callback)
    await state.set_state(PromoStates.waiting_code)
    text = await cms.t(db, "promo.prompt", locale)
    screen = renderer.Screen(
        text=text,
        markup=renderer.markup([await renderer.back_row(db, locale, with_main=False)]),
    )
    await ui.show(callback, screen, db=db)


@router.message(Command("promo"))
async def cmd_promo(
    message: Message, db: AsyncSession, locale: str, state: FSMContext
) -> None:
    await state.set_state(PromoStates.waiting_code)
    await sender.send_message(
        message.chat.id, await cms.t(db, "promo.prompt", locale)
    )


@router.message(PromoStates.waiting_code)
async def promo_code_entered(
    message: Message, db: AsyncSession, user: User, locale: str, state: FSMContext
) -> None:
    code = promo_service.normalize_code(message.text or "")
    result = await promo_service.validate(db, code=code, user=user)
    if not result.ok or result.promo is None:
        await sender.send_message(
            message.chat.id,
            await cms.t(db, "promo.invalid", locale, reason=result.reason or ""),
        )
        return

    await state.update_data(promo_code=code)
    await state.set_state(None)
    value = result.promo.discount_value
    suffix = "%" if str(getattr(result.promo.discount_type, "value", "")) == "percent" else ""
    await sender.send_message(
        message.chat.id,
        await cms.t(db, "promo.applied", locale, code=code, value=f"{value}{suffix}"),
    )


@router.callback_query(F.data == cb.pack(cb.SUPPORT))
async def cb_support(callback: CallbackQuery, db: AsyncSession, locale: str) -> None:
    await sender.answer_callback(callback)
    username = str(
        await cms.setting(db, "tg.support_username", settings.support_username) or ""
    ).lstrip("@")
    text = await cms.t(db, "support.text", locale, username=username)
    rows: list[list[InlineKeyboardButton]] = []
    if username:
        label = "Написать в поддержку" if locale == "ru" else "Contact support"
        rows.append(
            [InlineKeyboardButton(text=label, url="https://t.me/" + username)]
        )
    rows.append(await renderer.back_row(db, locale, with_main=False))
    await ui.show(
        callback, renderer.Screen(text=text, markup=renderer.markup(rows)), db=db
    )


@router.callback_query(F.data.startswith(cb.INFO + cb.SEP))
async def cb_info(callback: CallbackQuery, db: AsyncSession, locale: str) -> None:
    await sender.answer_callback(callback)
    _, args = cb.parse(callback.data)
    key = cb.arg_str(args, 0, "about")
    text = await cms.t(db, "info." + key, locale)
    if text.startswith("["):
        text = await cms.t(db, "info.about", locale)
    await ui.show(
        callback,
        renderer.Screen(
            text=text,
            markup=renderer.markup(
                [await renderer.back_row(db, locale, with_main=False)]
            ),
        ),
        db=db,
    )


@router.callback_query(F.data == cb.pack(cb.NOOP))
async def cb_noop(callback: CallbackQuery) -> None:
    await sender.answer_callback(callback)


@router.message(Command("help"))
async def cmd_help(message: Message, db: AsyncSession, locale: str) -> None:
    text = await cms.t(db, "info.about", locale)
    await sender.send_message(message.chat.id, text)


@router.callback_query()
async def cb_unknown(callback: CallbackQuery) -> None:
    """На любой устаревший callback отвечаем сразу, чтобы не висел спиннер."""
    await sender.answer_callback(callback)
    log.debug("bot.unknown_callback", data=callback.data)


@router.message()
async def message_fallback(
    message: Message, db: AsyncSession, user: User, locale: str
) -> None:
    """Любое нераспознанное сообщение ведёт в главное меню."""
    screen = await renderer.main_screen(db, locale, user=user)
    await ui.show(message, screen, db=db)
