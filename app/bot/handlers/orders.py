"""Мои заказы (ТЗ п.24)."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import callbacks as cb
from app.bot import renderer, sender, ui
from app.models import User
from app.services import cms, orders as orders_service

router = Router(name="orders")

PAGE_SIZE = 10


async def show_orders(
    event: Message | CallbackQuery, db: AsyncSession, user: User, locale: str
) -> None:
    rows = await orders_service.list_user_orders(db, user.id, limit=PAGE_SIZE)
    await ui.show(event, await renderer.orders_screen(db, locale, rows), db=db)


@router.message(Command("orders"))
async def cmd_orders(
    message: Message, db: AsyncSession, user: User, locale: str
) -> None:
    await show_orders(message, db, user, locale)


@router.callback_query(F.data.startswith(cb.ORDERS))
async def cb_orders(
    callback: CallbackQuery, db: AsyncSession, user: User, locale: str
) -> None:
    await sender.answer_callback(callback)
    await show_orders(callback, db, user, locale)


@router.callback_query(F.data.startswith(cb.ORDER + cb.SEP))
async def cb_order(
    callback: CallbackQuery, db: AsyncSession, user: User, locale: str
) -> None:
    await sender.answer_callback(callback)
    _, args = cb.parse(callback.data)
    order = await orders_service.get_order(db, cb.arg_int(args, 0))
    if order is None or order.user_id != user.id:
        await ui.toast(callback, await cms.t(db, "common.error", locale), alert=True)
        return
    await ui.show(callback, await renderer.order_screen(db, locale, order), db=db)
