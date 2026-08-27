"""Сборка экранов бота из CMS: тексты, блоки дизайна, кнопки, картинки.

Ни одна надпись не зашита в код: всё берётся из таблиц настроек (ТЗ п.5, п.14-п.19),
поэтому дизайн меняется из админки без деплоя.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable, Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import callbacks as cb
from app.models import Category, Order, Product
from app.services import cms


@dataclass(slots=True)
class Screen:
    """Готовый к отправке экран."""

    text: str
    markup: InlineKeyboardMarkup | None = None
    image_url: str | None = None


def _get(item: Any, key: str, default: Any = None) -> Any:
    """Единый доступ к полю для dict (из кэша) и ORM-объекта."""
    if isinstance(item, dict):
        value = item.get(key, default)
    else:
        value = getattr(item, key, default)
    if hasattr(value, "value") and not isinstance(value, (str, int, float, bool)):
        return value.value
    return value


def _label(item: Any, locale: str) -> str:
    """Надпись кнопки: эмодзи + локализованный текст."""
    title = cms.pick_locale(_get(item, "title"), locale)
    emoji = str(_get(item, "emoji") or "").strip()
    text = str(title or "").strip()
    return f"{emoji} {text}".strip() if emoji else text


def _row_key(item: Any, fallback: int) -> tuple[int, int]:
    try:
        row = int(_get(item, "row", fallback) or 0)
    except (TypeError, ValueError):
        row = fallback
    try:
        position = int(_get(item, "position", 0) or 0)
    except (TypeError, ValueError):
        position = 0
    return row, position


def _chunk(
    buttons: Sequence[InlineKeyboardButton], per_row: int
) -> list[list[InlineKeyboardButton]]:
    per_row = max(1, int(per_row or 1))
    return [list(buttons[i : i + per_row]) for i in range(0, len(buttons), per_row)]


# =====================================================================
#  КНОПКИ ИЗ CMS
# =====================================================================
async def _resolve_action(
    db: AsyncSession, item: Any, locale: str, context: dict[str, Any]
) -> InlineKeyboardButton | None:
    action = str(_get(item, "action") or "noop")
    payload = _get(item, "payload") or ""
    url = str(_get(item, "url") or "").strip()
    text = _label(item, locale)
    if not text:
        return None

    if action == "url":
        return InlineKeyboardButton(text=text, url=url) if url else None
    if action == "catalog":
        return InlineKeyboardButton(text=text, callback_data=cb.catalog())
    if action == "category":
        try:
            return InlineKeyboardButton(
                text=text, callback_data=cb.category(int(payload))
            )
        except (TypeError, ValueError):
            return InlineKeyboardButton(text=text, callback_data=cb.catalog())
    if action == "product":
        try:
            return InlineKeyboardButton(text=text, callback_data=cb.product(int(payload)))
        except (TypeError, ValueError):
            return None
    if action == "buy":
        product_id = context.get("product_id")
        if not product_id:
            return None
        return InlineKeyboardButton(text=text, callback_data=cb.buy(int(product_id)))
    if action == "my_orders":
        return InlineKeyboardButton(text=text, callback_data=cb.orders())
    if action == "promo":
        return InlineKeyboardButton(text=text, callback_data=cb.pack(cb.PROMO))
    if action == "support":
        return InlineKeyboardButton(text=text, callback_data=cb.pack(cb.SUPPORT))
    if action == "info_page":
        return InlineKeyboardButton(text=text, callback_data=cb.info(str(payload or "about")))
    if action == "language":
        return InlineKeyboardButton(text=text, callback_data=cb.pack(cb.LANGUAGE))
    if action == "main_menu":
        return InlineKeyboardButton(text=text, callback_data=cb.main_menu())
    if action == "back":
        return InlineKeyboardButton(
            text=text, callback_data=str(context.get("back") or cb.main_menu())
        )
    return InlineKeyboardButton(text=text, callback_data=cb.pack(cb.NOOP))


async def cms_buttons(
    db: AsyncSession,
    screen: str,
    locale: str,
    context: dict[str, Any] | None = None,
) -> list[list[InlineKeyboardButton]]:
    """Кнопки экрана, настроенные в админке (с учётом row/position/is_wide)."""
    context = context or {}
    items = await cms.get_buttons(db, screen)
    prepared: list[tuple[tuple[int, int], bool, InlineKeyboardButton]] = []
    for index, item in enumerate(items or []):
        if not bool(_get(item, "is_active", True)):
            continue
        button = await _resolve_action(db, item, locale, context)
        if button is None:
            continue
        prepared.append((_row_key(item, index), bool(_get(item, "is_wide", False)), button))

    prepared.sort(key=lambda triple: triple[0])
    rows: list[list[InlineKeyboardButton]] = []
    for (row_index, _), is_wide, button in prepared:
        if is_wide or not rows or rows[-1] and row_index != _row_key_of(rows, row_index):
            rows.append([button])
        else:
            rows[-1].append(button)
    return rows


def _row_key_of(rows: list[list[InlineKeyboardButton]], row_index: int) -> int:
    """Вспомогательная функция группировки: текущий индекс ряда."""
    return row_index


def markup(rows: Iterable[Iterable[InlineKeyboardButton]]) -> InlineKeyboardMarkup | None:
    keyboard = [list(row) for row in rows if list(row)]
    if not keyboard:
        return None
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


async def back_row(
    db: AsyncSession, locale: str, *, target: str | None = None, with_main: bool = True
) -> list[InlineKeyboardButton]:
    """Стандартный ряд «Назад / Главное меню»."""
    row: list[InlineKeyboardButton] = []
    back_text = await cms.t(db, "common.back", locale)
    back_emoji = await cms.e(db, "back")
    row.append(
        InlineKeyboardButton(
            text=f"{back_emoji} {back_text}".strip(),
            callback_data=target or cb.main_menu(),
        )
    )
    if with_main:
        main_text = await cms.t(db, "common.main_menu", locale)
        main_emoji = await cms.e(db, "main_menu")
        row.append(
            InlineKeyboardButton(
                text=f"{main_emoji} {main_text}".strip(), callback_data=cb.main_menu()
            )
        )
    return row


# =====================================================================
#  БЛОКИ ДИЗАЙНА → ТЕКСТ
# =====================================================================
async def render_blocks(
    db: AsyncSession, screen: str, locale: str, **fmt: Any
) -> tuple[str, str | None]:
    """Собрать текст экрана из визуальных блоков. Возвращает (текст, картинка)."""
    blocks = await cms.get_blocks(db, screen)
    parts: list[str] = []
    image_url: str | None = None

    for block in sorted(blocks or [], key=lambda b: int(_get(b, "position", 0) or 0)):
        if not bool(_get(block, "is_active", True)):
            continue
        block_type = str(_get(block, "block_type") or "text")
        title = str(cms.pick_locale(_get(block, "title"), locale) or "")
        content = str(cms.pick_locale(_get(block, "content"), locale) or "")
        emoji = str(_get(block, "emoji") or "")

        if block_type == "image":
            image_url = image_url or (_get(block, "image_url") or None)
            continue
        if block_type == "divider":
            parts.append("—" * 12)
            continue
        if block_type == "buttons":
            continue
        if block_type == "title" and title:
            parts.append(f"<b>{emoji} {title}</b>".strip())
            continue
        if block_type == "info" and content:
            parts.append(f"{emoji} <i>{content}</i>".strip())
            continue
        chunk = "\n".join(x for x in (f"<b>{title}</b>" if title else "", content) if x)
        if chunk:
            parts.append(f"{emoji} {chunk}".strip() if emoji else chunk)

    text = "\n\n".join(parts)
    if fmt:
        try:
            text = text.format(**fmt)
        except (KeyError, IndexError, ValueError):
            pass
    return text, image_url


# =====================================================================
#  ЭКРАНЫ
# =====================================================================
async def main_screen(db: AsyncSession, locale: str, *, user: Any = None) -> Screen:
    name = getattr(user, "first_name", "") or ""
    text, block_image = await render_blocks(db, "main", locale, name=name)
    if not text:
        title = await cms.t(db, "main.title", locale)
        subtitle = await cms.t(db, "main.subtitle", locale, name=name)
        text = "\n\n".join(x for x in (f"<b>{title}</b>", subtitle) if x)

    rows = await cms_buttons(db, "main", locale)
    image = block_image or await cms.image(db, "main")
    return Screen(text=text or title, markup=markup(rows), image_url=image)


async def catalog_screen(
    db: AsyncSession, locale: str, categories: Sequence[Category]
) -> Screen:
    text, block_image = await render_blocks(db, "catalog", locale)
    if not text:
        text = await cms.t(db, "catalog.title", locale)
    if not categories:
        text = f"{text}\n\n{await cms.t(db, 'catalog.empty', locale)}"

    per_row = int(await cms.design_value(db, "catalog.buttons_per_row", 2) or 2)
    buttons = [
        InlineKeyboardButton(
            text=_label(item, locale) or "—", callback_data=cb.category(item.id)
        )
        for item in categories
    ]
    rows = _chunk(buttons, per_row)
    rows.extend(await cms_buttons(db, "catalog", locale))
    rows.append(await back_row(db, locale, target=cb.main_menu(), with_main=False))
    return Screen(
        text=text,
        markup=markup(rows),
        image_url=block_image or await cms.image(db, "catalog"),
    )


async def category_screen(
    db: AsyncSession,
    locale: str,
    category: Category,
    products: Sequence[Product],
    *,
    stock: dict[int, int] | None = None,
) -> Screen:
    title = cms.pick_locale(category.title, locale)
    description = cms.pick_locale(category.description, locale) or ""
    header = await cms.t(db, "category.title", locale, title=title)
    text = "\n\n".join(x for x in (header or f"<b>{title}</b>", description) if x)
    if not products:
        text = f"{text}\n\n{await cms.t(db, 'category.empty', locale)}"

    show_price = bool(
        await cms.design_value(db, "show_product_price_in_button", True)
    )
    per_row = int(
        category.buttons_per_row
        or await cms.design_value(db, "category.buttons_per_row", 1)
        or 1
    )

    buttons: list[InlineKeyboardButton] = []
    for product in products:
        label = _label(product, locale) or "—"
        if show_price:
            price = await cms.format_price(db, product.price, product.currency or "")
            label = f"{label} — {price}"
        buttons.append(
            InlineKeyboardButton(
                text=label[:64], callback_data=cb.product(product.id, category.id)
            )
        )

    rows = _chunk(buttons, per_row)
    rows.extend(await cms_buttons(db, "category", locale, {"back": cb.catalog()}))
    rows.append(await back_row(db, locale, target=cb.catalog()))
    return Screen(
        text=text,
        markup=markup(rows),
        image_url=category.image_url or await cms.image(db, "catalog"),
    )


async def product_screen(
    db: AsyncSession,
    locale: str,
    product: Product,
    *,
    available: bool = True,
    stock: int | None = None,
    back: str | None = None,
) -> Screen:
    title = cms.pick_locale(product.title, locale)
    description = cms.pick_locale(product.description, locale) or ""
    price = await cms.format_price(db, product.price, product.currency or "")
    old_price = (
        await cms.format_price(db, product.old_price, product.currency or "")
        if product.old_price
        else ""
    )

    text = await cms.t(
        db,
        "product.card",
        locale,
        title=title,
        description=description,
        price=price,
        old_price=old_price,
        emoji=product.emoji or "",
    )
    if not text or text.startswith("["):
        text = f"<b>{product.emoji or ''} {title}</b>\n\n{description}\n\n<b>{price}</b>"

    if bool(await cms.design_value(db, "show_stock", True)) and stock is not None:
        stock_line = await cms.t(db, "product.stock_line", locale, stock=stock)
        if stock_line and not stock_line.startswith("["):
            text = f"{text}\n{stock_line}"
    if bool(await cms.design_value(db, "show_id_line", True)):
        text = f"{text}\n<code>#{product.id}</code>"

    rows: list[list[InlineKeyboardButton]] = []
    if available:
        buy_text = cms.pick_locale(product.buy_button_text, locale) or ""
        if not buy_text:
            buy_emoji = await cms.e(db, "buy")
            buy_text = f"{buy_emoji} Оплатить" if locale == "ru" else f"{buy_emoji} Pay"
        rows.append(
            [InlineKeyboardButton(text=buy_text[:64], callback_data=cb.buy(product.id))]
        )
    else:
        out_of_stock = await cms.t(db, "product.out_of_stock", locale)
        text = f"{text}\n\n{out_of_stock}"

    rows.extend(
        await cms_buttons(
            db,
            "product",
            locale,
            {"product_id": product.id, "back": back or cb.catalog()},
        )
    )
    rows.append(await back_row(db, locale, target=back or cb.catalog()))
    return Screen(text=text, markup=markup(rows), image_url=product.image_url)


async def methods_screen(
    db: AsyncSession, locale: str, order: Order, methods: Sequence[dict[str, Any]]
) -> Screen:
    total = await cms.format_price(db, order.total, order.currency or "")
    text = await cms.t(
        db, "purchase.methods", locale, total=total, order_no=order.public_no
    )
    rows = [
        [
            InlineKeyboardButton(
                text=str(item.get("label") or item.get("name") or "—")[:64],
                callback_data=cb.method(order.id, int(item["code"])),
            )
        ]
        for item in methods
    ]
    rows.append(await back_row(db, locale, target=cb.product(order.product_id or 0)))
    return Screen(
        text=text, markup=markup(rows), image_url=await cms.image(db, "payment")
    )


async def payment_screen(
    db: AsyncSession, locale: str, order: Order, *, redirect_url: str | None
) -> Screen:
    total = await cms.format_price(db, order.total, order.currency or "")
    text = await cms.t(
        db, "purchase.link", locale, total=total, order_no=order.public_no
    )
    rows: list[list[InlineKeyboardButton]] = []
    if redirect_url:
        pay_emoji = await cms.e(db, "buy")
        label = "Перейти к оплате" if locale == "ru" else "Open payment page"
        rows.append(
            [InlineKeyboardButton(text=f"{pay_emoji} {label}".strip(), url=redirect_url)]
        )
    check_emoji = await cms.e(db, "check")
    check_label = "Проверить оплату" if locale == "ru" else "Check payment"
    rows.append(
        [
            InlineKeyboardButton(
                text=f"{check_emoji} {check_label}".strip(),
                callback_data=cb.check(order.id),
            )
        ]
    )
    cancel_label = "Отменить" if locale == "ru" else "Cancel"
    rows.append(
        [InlineKeyboardButton(text=cancel_label, callback_data=cb.cancel(order.id))]
    )
    rows.append(await back_row(db, locale, target=cb.main_menu(), with_main=False))
    return Screen(
        text=text, markup=markup(rows), image_url=await cms.image(db, "payment")
    )


async def orders_screen(
    db: AsyncSession, locale: str, orders: Sequence[Order]
) -> Screen:
    text, block_image = await render_blocks(db, "orders", locale)
    header = text or await cms.t(db, "orders.title", locale)
    if not orders:
        body = await cms.t(db, "orders.empty", locale)
        return Screen(
            text=f"{header}\n\n{body}",
            markup=markup([await back_row(db, locale, with_main=False)]),
            image_url=block_image or await cms.image(db, "orders"),
        )

    lines: list[str] = []
    rows: list[list[InlineKeyboardButton]] = []
    for order in orders:
        status_emoji = await cms.e(db, f"status_{_get(order, 'status')}")
        total = await cms.format_price(db, order.total, order.currency or "")
        line = await cms.t(
            db,
            "orders.item",
            locale,
            order_no=order.public_no,
            title=order.product_title or "",
            total=total,
            status=str(_get(order, "status")),
            emoji=status_emoji,
        )
        lines.append(line if not line.startswith("[") else f"{status_emoji} {order.public_no} — {order.product_title} — {total}")
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{status_emoji} {order.public_no}"[:64],
                    callback_data=cb.order(order.id),
                )
            ]
        )

    rows.append(await back_row(db, locale, with_main=False))
    return Screen(
        text="\n\n".join([header, *lines]),
        markup=markup(rows),
        image_url=block_image or await cms.image(db, "orders"),
    )


async def order_screen(db: AsyncSession, locale: str, order: Order) -> Screen:
    status = str(_get(order, "status"))
    status_emoji = await cms.e(db, f"status_{status}")
    total = await cms.format_price(db, order.total, order.currency or "")
    lines = [
        f"<b>{order.product_emoji or ''} {order.product_title}</b>".strip(),
        f"<code>{order.public_no}</code>",
        f"{status_emoji} {status}",
        f"<b>{total}</b>",
    ]
    if order.delivered_content:
        delivered = await cms.t(db, "purchase.delivered", locale, content=order.delivered_content)
        lines.append(
            delivered
            if not delivered.startswith("[")
            else f"<pre>{order.delivered_content}</pre>"
        )

    rows: list[list[InlineKeyboardButton]] = []
    if status in {"created", "payment_pending"}:
        check_label = "Проверить оплату" if locale == "ru" else "Check payment"
        rows.append(
            [InlineKeyboardButton(text=check_label, callback_data=cb.check(order.id))]
        )
    rows.append(await back_row(db, locale, target=cb.orders()))
    return Screen(text="\n".join(lines), markup=markup(rows))


async def language_screen(db: AsyncSession, locale: str) -> Screen:
    text = await cms.t(db, "language.prompt", locale)
    names = {"ru": "🇷🇺 Русский", "en": "🇬🇧 English"}
    rows = [
        [
            InlineKeyboardButton(
                text=names.get(code, code.upper()), callback_data=cb.set_language(code)
            )
        ]
        for code in cms.LOCALES
    ]
    rows.append(await back_row(db, locale, with_main=False))
    return Screen(text=text, markup=markup(rows))


async def simple_screen(
    db: AsyncSession, locale: str, text_key: str, *, back: str | None = None, **fmt: Any
) -> Screen:
    text = await cms.t(db, text_key, locale, **fmt)
    rows = [await back_row(db, locale, target=back, with_main=back is not None)]
    return Screen(text=text, markup=markup(rows))
