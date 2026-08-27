"""Стартовое содержимое CMS (ТЗ п.30-п.36).

Всё, что видит пользователь в боте, задаётся здесь один раз при первом запуске
(seed.py), а дальше меняется только через админ-панель — без правки кода.
Формат текстов: {"ru": "...", "en": "..."}.
"""
from __future__ import annotations

from typing import Any

LOCALES: tuple[str, ...] = ("ru", "en")
DEFAULT_LOCALE = "ru"

# =====================================================================
#  ТЕКСТЫ
# =====================================================================
# key: (section, label, ru, en, is_html)
_TEXT_ROWS: tuple[tuple[str, str, str, str, str, bool], ...] = (
    (
        "start.welcome",
        "start",
        "Приветствие при /start",
        "<b>Добро пожаловать!</b>\nВыберите раздел ниже, чтобы начать покупки.",
        "<b>Welcome!</b>\nPick a section below to start shopping.",
        True,
    ),
    (
        "main.title",
        "main",
        "Заголовок главного экрана",
        "<b>Главное меню</b>",
        "<b>Main menu</b>",
        True,
    ),
    (
        "main.subtitle",
        "main",
        "Подзаголовок главного экрана",
        "Автоматическая выдача 24/7. Оплата СБП и криптовалютой.",
        "Instant delivery 24/7. Pay by SBP or crypto.",
        False,
    ),
    (
        "catalog.title",
        "catalog",
        "Заголовок каталога",
        "<b>Каталог</b>\nВыберите категорию:",
        "<b>Catalog</b>\nChoose a category:",
        True,
    ),
    (
        "catalog.empty",
        "catalog",
        "Каталог пуст",
        "Каталог пока пуст. Загляните позже.",
        "The catalog is empty for now. Please check back later.",
        False,
    ),
    (
        "category.title",
        "category",
        "Заголовок категории",
        "{emoji} <b>{title}</b>",
        "{emoji} <b>{title}</b>",
        True,
    ),
    (
        "category.empty",
        "category",
        "Категория пуста",
        "В этой категории пока нет товаров.",
        "No products in this category yet.",
        False,
    ),
    (
        "product.card",
        "product",
        "Карточка товара",
        "{emoji} <b>{title}</b>\n\n{description}\n\nЦена: <b>{price}</b>",
        "{emoji} <b>{title}</b>\n\n{description}\n\nPrice: <b>{price}</b>",
        True,
    ),
    (
        "product.out_of_stock",
        "product",
        "Товар закончился",
        "❗️ Товар временно закончился.",
        "❗️ This item is temporarily out of stock.",
        False,
    ),
    (
        "product.stock_line",
        "product",
        "Строка о наличии",
        "В наличии: {stock}",
        "In stock: {stock}",
        False,
    ),
    (
        "purchase.methods",
        "purchase",
        "Выбор способа оплаты",
        "Выберите способ оплаты для заказа <b>{order_no}</b> на сумму <b>{total}</b>:",
        "Choose a payment method for order <b>{order_no}</b>, total <b>{total}</b>:",
        True,
    ),
    (
        "purchase.creating",
        "purchase",
        "Создание платежа",
        "Создаю ссылку на оплату…",
        "Creating a payment link…",
        False,
    ),
    (
        "purchase.link",
        "purchase",
        "Ссылка на оплату",
        "💳 Заказ <b>{order_no}</b>\nСумма: <b>{total}</b>\nСпособ: {method}\n\nОплатите в течение {minutes} мин. После оплаты товар придёт автоматически.",
        "💳 Order <b>{order_no}</b>\nTotal: <b>{total}</b>\nMethod: {method}\n\nPay within {minutes} min. The item will be delivered automatically.",
        True,
    ),
    (
        "purchase.pending",
        "purchase",
        "Ожидание оплаты",
        "⏳ Оплата пока не подтверждена. Попробуйте проверить через минуту.",
        "⏳ Payment is not confirmed yet. Try checking again in a minute.",
        False,
    ),
    (
        "purchase.paid",
        "purchase",
        "Оплата получена",
        "✅ Оплата получена! Готовлю выдачу…",
        "✅ Payment received! Preparing your delivery…",
        False,
    ),
    (
        "purchase.delivered",
        "purchase",
        "Выдача товара",
        "🎁 Заказ <b>{order_no}</b> выполнен.\n\n{content}",
        "🎁 Order <b>{order_no}</b> is complete.\n\n{content}",
        True,
    ),
    (
        "purchase.manual",
        "purchase",
        "Выдача вручную",
        "✅ Оплата получена. Менеджер выдаст товар вручную в ближайшее время.",
        "✅ Payment received. A manager will deliver your item shortly.",
        False,
    ),
    (
        "purchase.failed",
        "purchase",
        "Оплата не удалась",
        "❌ Оплата не прошла. Попробуйте ещё раз или выберите другой способ.",
        "❌ Payment failed. Try again or choose another method.",
        False,
    ),
    (
        "purchase.expired",
        "purchase",
        "Срок оплаты истёк",
        "⏰ Срок оплаты заказа <b>{order_no}</b> истёк. Оформите новый заказ.",
        "⏰ Payment window for order <b>{order_no}</b> expired. Please create a new order.",
        True,
    ),
    (
        "orders.title",
        "orders",
        "Заголовок моих заказов",
        "<b>Мои заказы</b>",
        "<b>My orders</b>",
        True,
    ),
    (
        "orders.empty",
        "orders",
        "Нет заказов",
        "У вас пока нет заказов.",
        "You have no orders yet.",
        False,
    ),
    (
        "orders.item",
        "orders",
        "Строка заказа",
        "{status_emoji} <b>{order_no}</b> — {title}\n{total} · {created}",
        "{status_emoji} <b>{order_no}</b> — {title}\n{total} · {created}",
        True,
    ),
    (
        "promo.prompt",
        "promo",
        "Запрос промокода",
        "Введите промокод одним сообщением:",
        "Send your promo code in one message:",
        False,
    ),
    (
        "promo.applied",
        "promo",
        "Промокод применён",
        "✅ Промокод <b>{code}</b> применён. Скидка: <b>{discount}</b>.",
        "✅ Promo code <b>{code}</b> applied. Discount: <b>{discount}</b>.",
        True,
    ),
    (
        "promo.invalid",
        "promo",
        "Промокод не подходит",
        "❌ Промокод недействителен или уже использован.",
        "❌ This promo code is invalid or already used.",
        False,
    ),
    (
        "support.text",
        "info",
        "Текст поддержки",
        "Напишите нам, если нужна помощь — отвечаем круглосуточно.",
        "Message us any time — support works 24/7.",
        False,
    ),
    (
        "info.about",
        "info",
        "О магазине",
        "Мы продаём цифровые товары с мгновенной выдачей после оплаты.",
        "We sell digital goods with instant delivery after payment.",
        False,
    ),
    (
        "common.back",
        "common",
        "Кнопка назад",
        "Назад",
        "Back",
        False,
    ),
    (
        "common.main_menu",
        "common",
        "Кнопка главного меню",
        "Главное меню",
        "Main menu",
        False,
    ),
    (
        "common.error",
        "common",
        "Общая ошибка",
        "Что-то пошло не так. Попробуйте ещё раз.",
        "Something went wrong. Please try again.",
        False,
    ),
    (
        "common.rate_limited",
        "common",
        "Слишком быстро",
        "Не так быстро 🙂 Подождите секунду.",
        "Not so fast 🙂 Please wait a second.",
        False,
    ),
    (
        "common.blocked",
        "common",
        "Пользователь заблокирован",
        "Доступ к боту ограничен. Обратитесь в поддержку.",
        "Your access is restricted. Please contact support.",
        False,
    ),
    (
        "language.prompt",
        "common",
        "Выбор языка",
        "Выберите язык / Choose language:",
        "Choose language / Выберите язык:",
        False,
    ),
    (
        "language.changed",
        "common",
        "Язык изменён",
        "Язык изменён на русский.",
        "Language switched to English.",
        False,
    ),
    (
        "subscription.required",
        "common",
        "Требуется подписка",
        "Для покупок подпишитесь на наш канал и нажмите «Проверить».",
        "Subscribe to our channel and tap “Check” to continue.",
        False,
    ),
    (
        "payment.method.sbp_qr",
        "payment",
        "Метод оплаты: СБП QR",
        "🏦 СБП (QR)",
        "🏦 SBP (QR)",
        False,
    ),
    (
        "payment.method.card_c2c",
        "payment",
        "Метод оплаты: карта",
        "💳 Банковская карта",
        "💳 Bank card",
        False,
    ),
    (
        "payment.method.sbp",
        "payment",
        "Метод оплаты: СБП",
        "🏦 СБП",
        "🏦 SBP",
        False,
    ),
    (
        "payment.method.card_h2h",
        "payment",
        "Метод оплаты: карта H2H",
        "💳 Карта (H2H)",
        "💳 Card (H2H)",
        False,
    ),
    (
        "payment.method.crypto",
        "payment",
        "Метод оплаты: криптовалюта",
        "₿ Криптовалюта (USDT)",
        "₿ Crypto (USDT)",
        False,
    ),
    (
        "payment.method.card_intl",
        "payment",
        "Метод оплаты: международная карта",
        "🌍 Международная карта",
        "🌍 International card",
        False,
    ),
)

TEXTS: dict[str, dict[str, Any]] = {
    key: {
        "section": section,
        "label": label,
        "value": {"ru": ru, "en": en},
        "is_html": is_html,
    }
    for key, section, label, ru, en, is_html in _TEXT_ROWS
}

# =====================================================================
#  EMOJI
# =====================================================================
EMOJI: dict[str, dict[str, str]] = {
    "catalog": {"value": "🛒", "label": "Каталог", "section": "menu"},
    "orders": {"value": "📦", "label": "Мои заказы", "section": "menu"},
    "promo": {"value": "🎟", "label": "Промокод", "section": "menu"},
    "support": {"value": "💬", "label": "Поддержка", "section": "menu"},
    "info": {"value": "ℹ️", "label": "Информация", "section": "menu"},
    "language": {"value": "🌐", "label": "Язык", "section": "menu"},
    "back": {"value": "⬅️", "label": "Назад", "section": "nav"},
    "main_menu": {"value": "🏠", "label": "Главное меню", "section": "nav"},
    "buy": {"value": "💳", "label": "Оплатить", "section": "purchase"},
    "check": {"value": "🔄", "label": "Проверить оплату", "section": "purchase"},
    "status_created": {"value": "🆕", "label": "Заказ создан", "section": "status"},
    "status_pending": {"value": "⏳", "label": "Ожидание оплаты", "section": "status"},
    "status_paid": {"value": "💰", "label": "Оплачен", "section": "status"},
    "status_completed": {"value": "✅", "label": "Выполнен", "section": "status"},
    "status_failed": {"value": "❌", "label": "Ошибка", "section": "status"},
    "status_cancelled": {"value": "🚫", "label": "Отменён", "section": "status"},
}

# =====================================================================
#  ИЗОБРАЖЕНИЯ
# =====================================================================
IMAGES: dict[str, dict[str, Any]] = {
    "main": {"label": "Главный экран", "purpose": "Шапка главного меню", "url": None},
    "catalog": {"label": "Каталог", "purpose": "Шапка каталога", "url": None},
    "orders": {"label": "Мои заказы", "purpose": "Шапка списка заказов", "url": None},
    "payment": {"label": "Оплата", "purpose": "Экран выбора оплаты", "url": None},
    "success": {"label": "Успешная выдача", "purpose": "После выдачи товара", "url": None},
    "support": {"label": "Поддержка", "purpose": "Экран поддержки", "url": None},
}

# =====================================================================
#  КНОПКИ ЭКРАНОВ БОТА
# =====================================================================
# screen → список кнопок (row/position задают расположение, is_wide — на всю ширину)
BUTTONS: dict[str, list[dict[str, Any]]] = {
    "main": [
        {
            "code": "catalog",
            "title": {"ru": "Каталог", "en": "Catalog"},
            "emoji": "🛒",
            "action": "catalog",
            "row": 1,
            "position": 1,
            "is_wide": True,
        },
        {
            "code": "my_orders",
            "title": {"ru": "Мои заказы", "en": "My orders"},
            "emoji": "📦",
            "action": "my_orders",
            "row": 2,
            "position": 1,
        },
        {
            "code": "promo",
            "title": {"ru": "Промокод", "en": "Promo code"},
            "emoji": "🎟",
            "action": "promo",
            "row": 2,
            "position": 2,
        },
        {
            "code": "support",
            "title": {"ru": "Поддержка", "en": "Support"},
            "emoji": "💬",
            "action": "support",
            "row": 3,
            "position": 1,
        },
        {
            "code": "info",
            "title": {"ru": "О магазине", "en": "About"},
            "emoji": "ℹ️",
            "action": "info_page",
            "payload": "info.about",
            "row": 3,
            "position": 2,
        },
        {
            "code": "language",
            "title": {"ru": "Язык", "en": "Language"},
            "emoji": "🌐",
            "action": "language",
            "row": 4,
            "position": 1,
            "is_wide": True,
        },
    ],
    "catalog": [
        {
            "code": "main_menu",
            "title": {"ru": "Главное меню", "en": "Main menu"},
            "emoji": "🏠",
            "action": "main_menu",
            "row": 90,
            "position": 1,
            "is_wide": True,
        }
    ],
    "category": [
        {
            "code": "back",
            "title": {"ru": "Назад", "en": "Back"},
            "emoji": "⬅️",
            "action": "catalog",
            "row": 90,
            "position": 1,
        },
        {
            "code": "main_menu",
            "title": {"ru": "Главное меню", "en": "Main menu"},
            "emoji": "🏠",
            "action": "main_menu",
            "row": 90,
            "position": 2,
        },
    ],
    "product": [
        {
            "code": "main_menu",
            "title": {"ru": "Главное меню", "en": "Main menu"},
            "emoji": "🏠",
            "action": "main_menu",
            "row": 90,
            "position": 1,
            "is_wide": True,
        }
    ],
    "orders": [
        {
            "code": "main_menu",
            "title": {"ru": "Главное меню", "en": "Main menu"},
            "emoji": "🏠",
            "action": "main_menu",
            "row": 90,
            "position": 1,
            "is_wide": True,
        }
    ],
    "info": [
        {
            "code": "main_menu",
            "title": {"ru": "Главное меню", "en": "Main menu"},
            "emoji": "🏠",
            "action": "main_menu",
            "row": 90,
            "position": 1,
            "is_wide": True,
        }
    ],
}

# =====================================================================
#  ДИЗАЙН-БЛОКИ ЭКРАНОВ
# =====================================================================
DESIGN_BLOCKS: list[dict[str, Any]] = [
    {
        "screen": "main",
        "block_type": "image",
        "position": 10,
        "config": {"image_key": "main"},
        "title": {},
        "content": {},
    },
    {
        "screen": "main",
        "block_type": "title",
        "position": 20,
        "config": {"text_key": "main.title"},
        "title": {"ru": "Главное меню", "en": "Main menu"},
        "content": {},
    },
    {
        "screen": "main",
        "block_type": "text",
        "position": 30,
        "config": {"text_key": "main.subtitle"},
        "title": {},
        "content": {},
    },
    {
        "screen": "main",
        "block_type": "buttons",
        "position": 40,
        "config": {"screen": "main"},
        "title": {},
        "content": {},
    },
    {
        "screen": "catalog",
        "block_type": "title",
        "position": 10,
        "config": {"text_key": "catalog.title"},
        "title": {},
        "content": {},
    },
    {
        "screen": "catalog",
        "block_type": "buttons",
        "position": 20,
        "config": {"screen": "catalog", "source": "categories"},
        "title": {},
        "content": {},
    },
    {
        "screen": "product",
        "block_type": "image",
        "position": 10,
        "config": {"source": "product"},
        "title": {},
        "content": {},
    },
    {
        "screen": "product",
        "block_type": "text",
        "position": 20,
        "config": {"text_key": "product.card"},
        "title": {},
        "content": {},
    },
    {
        "screen": "product",
        "block_type": "cta",
        "position": 30,
        "config": {"action": "buy"},
        "title": {"ru": "💳 Оплатить", "en": "💳 Pay"},
        "content": {},
    },
]

# =====================================================================
#  ГЛОБАЛЬНЫЕ НАСТРОЙКИ ДИЗАЙНА
# =====================================================================
DESIGN: dict[str, Any] = {
    "main.buttons_per_row": 2,
    "catalog.buttons_per_row": 2,
    "category.buttons_per_row": 1,
    "show_product_price_in_button": True,
    "show_stock": True,
    "show_id_line": True,
    "currency_symbol": "₽",
    "price_format": "{amount} {symbol}",
    "parse_mode": "HTML",
    "delete_previous_message": True,
}

DESIGN_LABELS: dict[str, str] = {
    "main.buttons_per_row": "Кнопок в ряду — главное меню",
    "catalog.buttons_per_row": "Кнопок в ряду — каталог",
    "category.buttons_per_row": "Кнопок в ряду — товары в категории",
    "show_product_price_in_button": "Показывать цену на кнопке товара",
    "show_stock": "Показывать остаток на складе",
    "show_id_line": "Показывать номер заказа/товара",
    "currency_symbol": "Символ валюты",
    "price_format": "Формат цены",
    "parse_mode": "Режим разметки Telegram",
    "delete_previous_message": "Удалять предыдущее сообщение",
}

# =====================================================================
#  СИСТЕМНЫЕ НАСТРОЙКИ
# =====================================================================
GROUPS: dict[str, str] = {
    "shop": "Магазин",
    "telegram": "Telegram",
    "payment": "Платежи",
    "bot": "Поведение бота",
}

# key: (value, group, label, is_secret)
_SYSTEM_ROWS: tuple[tuple[str, Any, str, str, bool], ...] = (
    ("shop.name", "Premium Shop", "shop", "Название магазина", False),
    ("shop.logo_url", "", "shop", "Логотип (URL)", False),
    ("shop.favicon_url", "", "shop", "Favicon (URL)", False),
    ("shop.admin_title", "Админ-панель", "shop", "Заголовок админки", False),
    ("shop.domain", "", "shop", "Публичный домен (https://…)", False),
    ("shop.currency", "RUB", "shop", "Валюта по умолчанию", False),
    ("tg.bot_username", "", "telegram", "Username бота (без @)", False),
    ("tg.channel_url", "", "telegram", "Ссылка на канал", False),
    ("tg.channel_id", "", "telegram", "ID канала (-100…)", False),
    ("tg.require_subscription", False, "telegram", "Требовать подписку на канал", False),
    ("tg.support_username", "", "telegram", "Username поддержки (без @)", False),
    ("tg.agreement_url", "", "telegram", "Ссылка на оферту", False),
    ("tg.privacy_url", "", "telegram", "Ссылка на политику конфиденциальности", False),
    ("payment.enabled", True, "payment", "Принимать платежи", False),
    ("payment.provider", "platega", "payment", "Провайдер (platega / stub)", False),
    ("payment.test_mode", False, "payment", "Тестовый режим", False),
    ("payment.methods", [2, 13], "payment", "Разрешённые методы оплаты", False),
    ("payment.ttl_seconds", 900, "payment", "Срок жизни платежа, сек", False),
    ("payment.send_metadata", True, "payment", "Передавать metadata (антифрод)", False),
    ("payment.merchant_id", "", "payment", "Platega MerchantId", True),
    ("payment.secret", "", "payment", "Platega Secret (API-ключ)", True),
    ("payment.webhook_secret", "", "payment", "Секрет для подписи webhook", True),
    (
        "payment.webhook_require_signature",
        False,
        "payment",
        "Отклонять webhook без валидной подписи",
        False,
    ),
    ("payment.allowed_ips", "", "payment", "Белый список IP для webhook (через запятую)", False),
    ("bot.rate_limit_per_second", 3, "bot", "Лимит действий пользователя в секунду", False),
    ("bot.broadcast_rate", 20, "bot", "Сообщений в секунду при рассылке", False),
    ("bot.notify_admins_on_order", True, "bot", "Уведомлять админов о заказах", False),
)

SYSTEM: dict[str, dict[str, Any]] = {
    key: {"value": value, "group": group, "label": label, "is_secret": is_secret}
    for key, value, group, label, is_secret in _SYSTEM_ROWS
}

# =====================================================================
#  МЕТОДЫ ОПЛАТЫ (справочник для админки)
# =====================================================================
PAYMENT_METHODS: dict[int, dict[str, str]] = {
    2: {
        "name": "SBP_QR",
        "label_ru": "СБП QR",
        "label_en": "SBP QR",
        "text_key": "payment.method.sbp_qr",
    },
    3: {
        "name": "CARD_C2C",
        "label_ru": "Банковская карта",
        "label_en": "Bank card",
        "text_key": "payment.method.card_c2c",
    },
    11: {
        "name": "SBP_H2H",
        "label_ru": "СБП",
        "label_en": "SBP",
        "text_key": "payment.method.sbp",
    },
    12: {
        "name": "CARD_H2H",
        "label_ru": "Карта (H2H)",
        "label_en": "Card (H2H)",
        "text_key": "payment.method.card_h2h",
    },
    13: {
        "name": "CRYPTO",
        "label_ru": "Криптовалюта (USDT)",
        "label_en": "Crypto (USDT)",
        "text_key": "payment.method.crypto",
    },
    14: {
        "name": "CARD_INTL",
        "label_ru": "Международная карта",
        "label_en": "International card",
        "text_key": "payment.method.card_intl",
    },
}

# =====================================================================
#  ДЕМО-КАТАЛОГ (создаётся только на пустой БД)
# =====================================================================
DEMO_CATEGORIES: list[dict[str, Any]] = [
    {
        "slug": "subscriptions",
        "title": {"ru": "Подписки", "en": "Subscriptions"},
        "description": {"ru": "Популярные сервисы", "en": "Popular services"},
        "emoji": "🎬",
        "sort_order": 10,
        "buttons_per_row": 1,
    },
    {
        "slug": "keys",
        "title": {"ru": "Ключи и аккаунты", "en": "Keys & accounts"},
        "description": {"ru": "Мгновенная выдача", "en": "Instant delivery"},
        "emoji": "🔑",
        "sort_order": 20,
        "buttons_per_row": 1,
    },
]

DEMO_PRODUCTS: list[dict[str, Any]] = [
    {
        "category_slug": "subscriptions",
        "slug": "demo-subscription-1m",
        "title": {"ru": "Демо-подписка, 1 месяц", "en": "Demo subscription, 1 month"},
        "description": {
            "ru": "Тестовый товар. После оплаты придёт инструкция.",
            "en": "Test product. Instructions are delivered after payment.",
        },
        "emoji": "🎬",
        "price": "499.00",
        "delivery_type": "static_text",
        "delivery_payload": {
            "text": {
                "ru": "Спасибо за покупку! Инструкция: замените этот текст в админ-панели.",
                "en": "Thanks for your purchase! Replace this text in the admin panel.",
            }
        },
        "sort_order": 10,
    },
    {
        "category_slug": "keys",
        "slug": "demo-key",
        "title": {"ru": "Демо-ключ", "en": "Demo key"},
        "description": {
            "ru": "Выдаётся уникальная единица со склада.",
            "en": "A unique inventory item is delivered.",
        },
        "emoji": "🔑",
        "price": "149.00",
        "delivery_type": "inventory",
        "delivery_payload": {},
        "sort_order": 20,
        "inventory": ["DEMO-KEY-0001", "DEMO-KEY-0002", "DEMO-KEY-0003"],
    },
]
