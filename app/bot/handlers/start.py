"""/start, главное меню и выбор языка (ТЗ п.13, п.19)."""
from __future__ import annotations

import structlog
from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import callbacks as cb
from app.bot import renderer, sender, ui
from app.core.config import settings
from app.models import User
from app.services import cms, users as users_service

log = structlog.get_logger(__name__)
router = Router(name="start")


async def _subscription_ok(db: AsyncSession, user: User) -> bool:
    """Проверка обязательной подписки на канал (ТЗ п.28)."""
    required = bool(await cms.setting(db, "tg.require_subscription", False))
    if not required:
        return True
    channel_id = str(
        await cms.setting(db, "tg.channel_id", settings.required_channel_id) or ""
    ).strip()
    if not channel_id:
        return True
    bot = sender.get_bot()
    if bot is None:
        return True
    try:
        member = await bot.get_chat_member(chat_id=channel_id, user_id=user.tg_id)
    except Exception as exc:  # канал недоступен — не блокируем пользователя
        log.warning("subscription.check_failed", error=str(exc))
        return True
    return str(getattr(member, "status", "")) in {
        "creator",
        "administrator",
        "member",
        "restricted",
    }


async def _subscription_screen(db: AsyncSession, locale: str) -> renderer.Screen:
    text = await cms.t(db, "subscription.required", locale)
    url = str(
        await cms.setting(db, "tg.channel_url", settings.required_channel_url) or ""
    ).strip()
    rows: list[list[InlineKeyboardButton]] = []
    if url:
        label = "Подписаться" if locale == "ru" else "Subscribe"
        rows.append([InlineKeyboardButton(text=label, url=url)])
    check = "Я подписался" if locale == "ru" else "I subscribed"
    rows.append(
        [InlineKeyboardButton(text=check, callback_data=cb.pack(cb.SUBSCRIPTION))]
    )
    return renderer.Screen(
        text=text, markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )


async def show_main(
    event: Message | CallbackQuery, db: AsyncSession, user: User, locale: str
) -> None:
    if not await _subscription_ok(db, user):
        await ui.show(event, await _subscription_screen(db, locale), db=db)
        return
    screen = await renderer.main_screen(db, locale, user=user)
    await ui.show(event, screen, db=db)


@router.message(CommandStart())
async def cmd_start(
    message: Message, db: AsyncSession, user: User, locale: str
) -> None:
    welcome = await cms.t(db, "start.welcome", locale, name=user.first_name or "")
    if welcome and not welcome.startswith("["):
        await sender.send_message(message.chat.id, welcome)
    await show_main(message, db, user, locale)


@router.message(Command("menu"))
async def cmd_menu(message: Message, db: AsyncSession, user: User, locale: str) -> None:
    await show_main(message, db, user, locale)


@router.message(Command("language"))
async def cmd_language(message: Message, db: AsyncSession, locale: str) -> None:
    await ui.show(message, await renderer.language_screen(db, locale), db=db)


@router.callback_query(F.data == cb.pack(cb.MAIN))
async def cb_main(
    callback: CallbackQuery, db: AsyncSession, user: User, locale: str
) -> None:
    await sender.answer_callback(callback)
    await show_main(callback, db, user, locale)


@router.callback_query(F.data == cb.pack(cb.SUBSCRIPTION))
async def cb_subscription(
    callback: CallbackQuery, db: AsyncSession, user: User, locale: str
) -> None:
    await sender.answer_callback(callback)
    if await _subscription_ok(db, user):
        await show_main(callback, db, user, locale)
        return
    await ui.toast(callback, await cms.t(db, "subscription.required", locale), alert=True)


@router.callback_query(F.data == cb.pack(cb.LANGUAGE))
async def cb_language(callback: CallbackQuery, db: AsyncSession, locale: str) -> None:
    await sender.answer_callback(callback)
    await ui.show(callback, await renderer.language_screen(db, locale), db=db)


@router.callback_query(F.data.startswith(cb.SET_LANGUAGE + cb.SEP))
async def cb_set_language(
    callback: CallbackQuery, db: AsyncSession, user: User
) -> None:
    await sender.answer_callback(callback)
    _, args = cb.parse(callback.data)
    new_locale = cms.normalize_locale(cb.arg_str(args, 0, cms.DEFAULT_LOCALE))
    await users_service.set_language(db, user, new_locale)
    changed = await cms.t(db, "language.changed", new_locale)
    if changed and not changed.startswith("["):
        await ui.toast(callback, changed)
    await show_main(callback, db, user, new_locale)
