"""Протокол callback_data бота.

Telegram ограничивает callback_data 64 байтами, поэтому используем короткие
коды действий и числовые id вместо slug'ов.
"""
from __future__ import annotations

SEP = ":"
MAX_LEN = 64

# Действия
MAIN = "m"
CATALOG = "cat"
CATEGORY = "c"
PRODUCT = "p"
BUY = "buy"
METHOD = "pm"
CHECK = "chk"
CANCEL = "cnl"
ORDERS = "ords"
ORDER = "o"
PROMO = "pr"
PROMO_CLEAR = "prc"
SUPPORT = "sup"
INFO = "i"
LANGUAGE = "lng"
SET_LANGUAGE = "slng"
BACK = "b"
NOOP = "noop"
SUBSCRIPTION = "sub"
URL = "url"


def pack(action: str, *args: object) -> str:
    """Собрать callback_data. При переполнении обрезаем до лимита Telegram."""
    parts = [str(action)]
    parts.extend("" if arg is None else str(arg) for arg in args)
    data = SEP.join(parts)
    return data[:MAX_LEN]


def parse(data: str | None) -> tuple[str, list[str]]:
    """Разобрать callback_data на (action, args)."""
    if not data:
        return NOOP, []
    chunks = str(data).split(SEP)
    return chunks[0], chunks[1:]


def arg_int(args: list[str], index: int, default: int = 0) -> int:
    """Безопасно взять целое число из аргументов."""
    try:
        return int(args[index])
    except (IndexError, TypeError, ValueError):
        return default


def arg_str(args: list[str], index: int, default: str = "") -> str:
    try:
        value = args[index]
    except IndexError:
        return default
    return value or default


# Готовые хелперы для читаемости в рендере
def main_menu() -> str:
    return pack(MAIN)


def catalog(page: int = 0) -> str:
    return pack(CATALOG, page)


def category(category_id: int, page: int = 0) -> str:
    return pack(CATEGORY, category_id, page)


def product(product_id: int, category_id: int = 0) -> str:
    return pack(PRODUCT, product_id, category_id)


def buy(product_id: int) -> str:
    return pack(BUY, product_id)


def method(order_id: int, method_code: int) -> str:
    return pack(METHOD, order_id, method_code)


def check(order_id: int) -> str:
    return pack(CHECK, order_id)


def cancel(order_id: int) -> str:
    return pack(CANCEL, order_id)


def orders(page: int = 0) -> str:
    return pack(ORDERS, page)


def order(order_id: int) -> str:
    return pack(ORDER, order_id)


def set_language(locale: str) -> str:
    return pack(SET_LANGUAGE, locale)


def info(key: str) -> str:
    return pack(INFO, key)
