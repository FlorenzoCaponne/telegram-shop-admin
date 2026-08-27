"""Каталог: категории и товары (ТЗ п.14-п.16).

Сценарий строго по ТЗ: КАТЕГОРИЯ → ТОВАР → ЦЕНА → ОПЛАТИТЬ. Без корзины.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import callbacks as cb
from app.bot import renderer, sender, ui
from app.services import catalog as catalog_service, cms

router = Router(name="catalog")


async def show_catalog(
    event: Message | CallbackQuery, db: AsyncSession, locale: str
) -> None:
    categories = await catalog_service.active_categories(db)
    if len(categories) == 1:
        # Одна категория — не заставляем делать лишний шаг.
        await show_category(event, db, locale, categories[0].id)
        return
    screen = await renderer.catalog_screen(db, locale, categories)
    await ui.show(event, screen, db=db)


async def show_category(
    event: Message | CallbackQuery, db: AsyncSession, locale: str, category_id: int
) -> None:
    category = await catalog_service.get_category(db, category_id)
    if category is None or not category.is_active:
        await ui.toast(event, await cms.t(db, "common.error", locale), alert=True)
        return
    products = await catalog_service.active_products(db, category_id=category_id)
    stock = await catalog_service.stock_map(db, [p.id for p in products])
    screen = await renderer.category_screen(
        db, locale, category, products, stock=stock
    )
    await ui.show(event, screen, db=db)


async def show_product(
    event: Message | CallbackQuery,
    db: AsyncSession,
    locale: str,
    product_id: int,
    *,
    back: str | None = None,
) -> None:
    product = await catalog_service.get_product(db, product_id)
    if product is None or not product.is_active:
        await ui.toast(event, await cms.t(db, "common.error", locale), alert=True)
        return
    available = await catalog_service.is_available(db, product)
    stock = await catalog_service.stock(db, product.id)
    screen = await renderer.product_screen(
        db,
        locale,
        product,
        available=available,
        stock=stock,
        back=back or (cb.category(product.category_id) if product.category_id else cb.catalog()),
    )
    await ui.show(event, screen, db=db)


@router.message(Command("catalog"))
async def cmd_catalog(message: Message, db: AsyncSession, locale: str) -> None:
    await show_catalog(message, db, locale)


@router.callback_query(F.data.startswith(cb.CATALOG + cb.SEP))
@router.callback_query(F.data == cb.CATALOG)
async def cb_catalog(callback: CallbackQuery, db: AsyncSession, locale: str) -> None:
    await sender.answer_callback(callback)
    await show_catalog(callback, db, locale)


@router.callback_query(F.data.startswith(cb.CATEGORY + cb.SEP))
async def cb_category(callback: CallbackQuery, db: AsyncSession, locale: str) -> None:
    await sender.answer_callback(callback)
    _, args = cb.parse(callback.data)
    await show_category(callback, db, locale, cb.arg_int(args, 0))


@router.callback_query(F.data.startswith(cb.PRODUCT + cb.SEP))
async def cb_product(callback: CallbackQuery, db: AsyncSession, locale: str) -> None:
    await sender.answer_callback(callback)
    _, args = cb.parse(callback.data)
    product_id = cb.arg_int(args, 0)
    category_id = cb.arg_int(args, 1)
    await show_product(
        callback,
        db,
        locale,
        product_id,
        back=cb.category(category_id) if category_id else None,
    )
