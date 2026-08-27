"""Покупка: выбор метода → счёт → проверка оплаты → автовыдача (ТЗ п.17-п.23).

Статус платежа всегда подтверждается запросом к провайдеру, а не доверием к webhook.
Выдача идемпотентна: повторное нажатие «Проверить» не выдаст товар дважды.
"""
from __future__ import annotations

from decimal import Decimal

import structlog
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import callbacks as cb
from app.bot import renderer, sender, ui
from app.models import Order, OrderStatus, User
from app.services import (
    catalog as catalog_service,
    cms,
    orders as orders_service,
    payments as payments_service,
    promo as promo_service,
)

log = structlog.get_logger(__name__)
router = Router(name="purchase")


async def _notify_admins_new_order(db: AsyncSession, order: Order, user: User) -> None:
    if not bool(await cms.setting(db, "bot.notify_admins_on_order", True)):
        return
    total = await cms.format_price(db, order.total, order.currency or "")
    username = f"@{user.username}" if user.username else str(user.tg_id)
    await sender.notify_admins(
        f"🛒 <b>Оплачен заказ</b> <code>{order.public_no}</code>\n"
        f"Товар: {order.product_title}\nСумма: {total}\nПокупатель: {username}"
    )


async def _deliver_and_show(
    callback: CallbackQuery, db: AsyncSession, order: Order, locale: str, user: User
) -> None:
    """После подтверждённой оплаты — выдать товар."""
    paid_text = await cms.t(db, "purchase.paid", locale, order_no=order.public_no)
    if paid_text and not paid_text.startswith("["):
        await ui.toast(callback, paid_text)

    result = await orders_service.deliver(db, order)
    await db.refresh(order)

    if result.ok and result.content:
        text = await cms.t(db, "purchase.delivered", locale, content=result.content)
        if not text or text.startswith("["):
            text = f"<pre>{result.content}</pre>"
    elif result.manual:
        text = await cms.t(db, "purchase.manual", locale, order_no=order.public_no)
        await sender.notify_admins(
            f"⚠️ Заказ <code>{order.public_no}</code> требует ручной выдачи."
        )
    else:
        text = await cms.t(db, "purchase.failed", locale, reason=result.reason or "")
        await sender.notify_admins(
            f"❌ Не удалось выдать заказ <code>{order.public_no}</code>: {result.reason}"
        )

    screen = renderer.Screen(
        text=text,
        markup=renderer.markup([await renderer.back_row(db, locale, with_main=False)]),
        image_url=await cms.image(db, "success") if result.ok else None,
    )
    await ui.show(callback, screen, db=db)
    await _notify_admins_new_order(db, order, user)


@router.callback_query(F.data.startswith(cb.BUY + cb.SEP))
async def cb_buy(
    callback: CallbackQuery,
    db: AsyncSession,
    user: User,
    locale: str,
    state: FSMContext,
) -> None:
    await sender.answer_callback(callback)
    _, args = cb.parse(callback.data)
    product = await catalog_service.get_product(db, cb.arg_int(args, 0))
    if product is None or not product.is_active:
        await ui.toast(callback, await cms.t(db, "common.error", locale), alert=True)
        return
    if not await catalog_service.is_available(db, product):
        await ui.toast(
            callback, await cms.t(db, "product.out_of_stock", locale), alert=True
        )
        return

    # Если пользователь ранее ввёл промокод — проверяем его заново для этого товара.
    data = await state.get_data()
    promo_code = str(data.get("promo_code") or "").strip()
    discount = Decimal("0")
    promo_id = None
    if promo_code:
        check = await promo_service.validate(
            db, code=promo_code, user=user, product=product, amount=product.price
        )
        if check.ok and check.promo is not None:
            discount = check.discount
            promo_id = check.promo.id
        else:
            await state.update_data(promo_code=None)

    ttl = int(await cms.setting(db, "payment.ttl_seconds", 900) or 900)
    order = await orders_service.create_order(
        db,
        user=user,
        product=product,
        locale=locale,
        discount_amount=discount or None,
        promo_code_id=promo_id,
        ttl_seconds=ttl,
    )

    methods = await payments_service.available_methods(db, locale)
    if product.payment_methods:
        allowed = {int(code) for code in product.payment_methods}
        methods = [item for item in methods if int(item["code"]) in allowed] or methods
    if not methods:
        await ui.toast(callback, await cms.t(db, "common.error", locale), alert=True)
        return

    if len(methods) == 1:
        await _create_payment(callback, db, order, locale, int(methods[0]["code"]))
        return

    await ui.show(
        callback, await renderer.methods_screen(db, locale, order, methods), db=db
    )


async def _create_payment(
    callback: CallbackQuery,
    db: AsyncSession,
    order: Order,
    locale: str,
    method_code: int,
) -> None:
    creating = await cms.t(db, "purchase.creating", locale)
    if creating and not creating.startswith("["):
        await ui.toast(callback, creating)

    result = await payments_service.start_payment(
        db, order=order, method_code=method_code
    )
    if not result.ok:
        await ui.show(
            callback,
            renderer.Screen(
                text=await cms.t(db, "purchase.failed", locale, reason=result.error or ""),
                markup=renderer.markup(
                    [await renderer.back_row(db, locale, with_main=False)]
                ),
            ),
            db=db,
        )
        return

    await ui.show(
        callback,
        await renderer.payment_screen(
            db, locale, order, redirect_url=result.redirect_url
        ),
        db=db,
    )


@router.callback_query(F.data.startswith(cb.METHOD + cb.SEP))
async def cb_method(
    callback: CallbackQuery, db: AsyncSession, user: User, locale: str
) -> None:
    await sender.answer_callback(callback)
    _, args = cb.parse(callback.data)
    order = await orders_service.get_order(db, cb.arg_int(args, 0))
    if order is None or order.user_id != user.id:
        await ui.toast(callback, await cms.t(db, "common.error", locale), alert=True)
        return
    if order.status in orders_service.TERMINAL_STATUSES:
        await ui.toast(callback, await cms.t(db, "purchase.expired", locale), alert=True)
        return
    await _create_payment(callback, db, order, locale, cb.arg_int(args, 1))


@router.callback_query(F.data.startswith(cb.CHECK + cb.SEP))
async def cb_check(
    callback: CallbackQuery, db: AsyncSession, user: User, locale: str
) -> None:
    await sender.answer_callback(callback)
    _, args = cb.parse(callback.data)
    order = await orders_service.get_order(db, cb.arg_int(args, 0))
    if order is None or order.user_id != user.id:
        await ui.toast(callback, await cms.t(db, "common.error", locale), alert=True)
        return

    if order.status in {OrderStatus.PAID, OrderStatus.PROCESSING}:
        await _deliver_and_show(callback, db, order, locale, user)
        return
    if order.status == OrderStatus.COMPLETED:
        await ui.show(callback, await renderer.order_screen(db, locale, order), db=db)
        return

    result = await payments_service.sync_order_payment(db, order)
    await db.refresh(order)

    if result.just_paid or order.status in {OrderStatus.PAID, OrderStatus.PROCESSING}:
        await _deliver_and_show(callback, db, order, locale, user)
        return
    if result.failed:
        await ui.show(
            callback,
            renderer.Screen(
                text=await cms.t(
                    db, "purchase.failed", locale, reason=order.failure_reason or ""
                ),
                markup=renderer.markup(
                    [await renderer.back_row(db, locale, with_main=False)]
                ),
            ),
            db=db,
        )
        return

    await ui.toast(callback, await cms.t(db, "purchase.pending", locale))


@router.callback_query(F.data.startswith(cb.CANCEL + cb.SEP))
async def cb_cancel(
    callback: CallbackQuery, db: AsyncSession, user: User, locale: str
) -> None:
    await sender.answer_callback(callback)
    _, args = cb.parse(callback.data)
    order = await orders_service.get_order(db, cb.arg_int(args, 0))
    if order is None or order.user_id != user.id:
        await ui.toast(callback, await cms.t(db, "common.error", locale), alert=True)
        return
    await orders_service.cancel_order(db, order)
    screen = await renderer.main_screen(db, locale, user=user)
    await ui.show(callback, screen, db=db)
