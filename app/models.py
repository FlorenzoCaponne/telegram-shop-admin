"""Все модели PostgreSQL (ТЗ п.39).

Ключевые принципы:
* Весь контент (тексты/emoji/фото/кнопки/дизайн) живёт в БД, а не в коде.
* Мультиязычные поля — JSONB вида {"ru": "...", "en": "..."}.
* Заказ хранит snapshot товара и цены — история неизменна (ТЗ п.19, п.52).
* Склад и webhook защищены от двойной выдачи на уровне СУБД (ТЗ п.50, п.51).
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.security import AdminRole
from app.db.base import Base, IntPK, JSONType, TimestampMixin


def _enum(enum_cls: type[StrEnum], name: str) -> SAEnum:
    """VARCHAR + CHECK вместо нативного ENUM: миграции гораздо проще."""
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        values_callable=lambda e: [m.value for m in e],
        length=32,
    )


# =====================================================================
#  ПЕРЕЧИСЛЕНИЯ
# =====================================================================
class OrderStatus(StrEnum):
    CREATED = "created"
    PAYMENT_PENDING = "payment_pending"
    PAID = "paid"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PaymentStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELED = "canceled"
    CHARGEBACKED = "chargebacked"
    EXPIRED = "expired"
    ERROR = "error"


class DeliveryType(StrEnum):
    STATIC_TEXT = "static_text"  # один и тот же текст/инструкция всем
    INVENTORY = "inventory"  # уникальная единица со склада
    MANUAL = "manual"  # выдаёт админ вручную


class InventoryStatus(StrEnum):
    AVAILABLE = "available"
    RESERVED = "reserved"
    DELIVERED = "delivered"


class DiscountType(StrEnum):
    PERCENT = "percent"
    FIXED = "fixed"


class BroadcastStatus(StrEnum):
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    DONE = "done"
    CANCELLED = "cancelled"


class BlockType(StrEnum):
    IMAGE = "image"
    TITLE = "title"
    TEXT = "text"
    INFO = "info"
    BUTTONS = "buttons"
    CTA = "cta"
    DIVIDER = "divider"


class ButtonAction(StrEnum):
    CATALOG = "catalog"
    CATEGORY = "category"
    PRODUCT = "product"
    BUY = "buy"
    MY_ORDERS = "my_orders"
    PROMO = "promo"
    SUPPORT = "support"
    INFO_PAGE = "info_page"
    URL = "url"
    LANGUAGE = "language"
    MAIN_MENU = "main_menu"
    BACK = "back"
    NOOP = "noop"


# =====================================================================
#  ПОЛЬЗОВАТЕЛИ И АДМИНЫ
# =====================================================================
class User(Base, IntPK, TimestampMixin):
    __tablename__ = "users"

    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), index=True)
    first_name: Mapped[str | None] = mapped_column(String(128))
    last_name: Mapped[str | None] = mapped_column(String(128))
    language: Mapped[str] = mapped_column(String(8), default="ru", server_default="ru")
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    bot_blocked: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )  # пользователь заблокировал бота
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    orders_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_spent: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=Decimal("0"), server_default="0"
    )
    notes: Mapped[str | None] = mapped_column(Text)

    orders: Mapped[list[Order]] = relationship(back_populates="user", lazy="raise")


class Admin(Base, IntPK, TimestampMixin):
    __tablename__ = "admins"

    login: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[AdminRole] = mapped_column(
        _enum(AdminRole, "admin_role"), default=AdminRole.MANAGER
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


# =====================================================================
#  КАТАЛОГ
# =====================================================================
class Category(Base, IntPK, TimestampMixin):
    __tablename__ = "categories"

    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    title: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    description: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    emoji: Mapped[str] = mapped_column(String(16), default="📂", server_default="📂")
    image_url: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    buttons_per_row: Mapped[int] = mapped_column(Integer, default=2, server_default="2")

    products: Mapped[list[Product]] = relationship(
        back_populates="category", lazy="selectin"
    )

    __table_args__ = (
        Index("ix_categories_active_order", "is_active", "sort_order"),
        CheckConstraint("buttons_per_row BETWEEN 1 AND 4", name="cat_buttons_per_row"),
    )


class Product(Base, IntPK, TimestampMixin):
    __tablename__ = "products"

    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), index=True
    )
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    title: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    description: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    emoji: Mapped[str] = mapped_column(String(16), default="🛍", server_default="🛍")
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    old_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(8), default="RUB", server_default="RUB")
    image_url: Mapped[str | None] = mapped_column(Text)
    buy_button_text: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    extra_blocks: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    delivery_type: Mapped[DeliveryType] = mapped_column(
        _enum(DeliveryType, "delivery_type"), default=DeliveryType.STATIC_TEXT
    )
    delivery_payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    payment_methods: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    sort_order: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    sales_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    category: Mapped[Category | None] = relationship(back_populates="products", lazy="joined")

    __table_args__ = (
        Index("ix_products_cat_active_order", "category_id", "is_active", "sort_order"),
        CheckConstraint("price >= 0", name="product_price_non_negative"),
    )


class InventoryItem(Base, IntPK):
    """Склад цифровых единиц (ТЗ п.24, п.25)."""

    __tablename__ = "inventory_items"

    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[InventoryStatus] = mapped_column(
        _enum(InventoryStatus, "inventory_status"), default=InventoryStatus.AVAILABLE
    )
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"), index=True
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    reserved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # Критично для быстрого SELECT ... FOR UPDATE SKIP LOCKED
        Index("ix_inventory_pick", "product_id", "status", "id"),
    )


# =====================================================================
#  ЗАКАЗЫ И ПЛАТЕЖИ
# =====================================================================
class Order(Base, IntPK, TimestampMixin):
    __tablename__ = "orders"

    public_no: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"), index=True
    )

    # ---- snapshot: не меняется даже если товар переименован/удалён ----
    product_title: Mapped[str] = mapped_column(String(255))
    product_emoji: Mapped[str] = mapped_column(String(16), default="🛍")
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    discount_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0"), server_default="0"
    )
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(8), default="RUB", server_default="RUB")

    status: Mapped[OrderStatus] = mapped_column(
        _enum(OrderStatus, "order_status"), default=OrderStatus.CREATED, index=True
    )
    promo_code_id: Mapped[int | None] = mapped_column(
        ForeignKey("promo_codes.id", ondelete="SET NULL")
    )
    delivered_content: Mapped[str | None] = mapped_column(Text)
    locale: Mapped[str] = mapped_column(String(8), default="ru", server_default="ru")
    failure_reason: Mapped[str | None] = mapped_column(String(255))

    paid_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )

    user: Mapped[User] = relationship(back_populates="orders", lazy="joined")
    payments: Mapped[list[Payment]] = relationship(
        back_populates="order", lazy="selectin", order_by="Payment.id"
    )

    __table_args__ = (
        Index("ix_orders_status_created", "status", "created_at"),
        Index("ix_orders_user_created", "user_id", "created_at"),
        CheckConstraint("total >= 0", name="order_total_non_negative"),
    )


class Payment(Base, IntPK, TimestampMixin):
    __tablename__ = "payments"

    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32), default="platega")
    provider_txn_id: Mapped[str | None] = mapped_column(String(128), index=True)
    method_code: Mapped[int | None] = mapped_column(Integer)  # Platega paymentMethod
    method_name: Mapped[str | None] = mapped_column(String(64))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(8), default="RUB")
    status: Mapped[PaymentStatus] = mapped_column(
        _enum(PaymentStatus, "payment_status"), default=PaymentStatus.PENDING, index=True
    )
    redirect_url: Mapped[str | None] = mapped_column(Text)
    qr_payload: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    confirmed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    check_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error_message: Mapped[str | None] = mapped_column(Text)
    # Сырой ответ провайдера без секретов — для разбора инцидентов
    raw_response: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)

    order: Mapped[Order] = relationship(back_populates="payments", lazy="joined")

    __table_args__ = (
        UniqueConstraint("provider", "provider_txn_id", name="uq_payments_provider_txn"),
    )


class PaymentEvent(Base, IntPK):
    """Журнал webhook/callback-событий. UNIQUE = защита от повторной обработки (ТЗ п.51)."""

    __tablename__ = "payment_events"

    provider: Mapped[str] = mapped_column(String(32), default="platega")
    event_key: Mapped[str] = mapped_column(String(255))
    payment_id: Mapped[int | None] = mapped_column(
        ForeignKey("payments.id", ondelete="SET NULL")
    )
    status_reported: Mapped[str | None] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    signature_valid: Mapped[bool] = mapped_column(Boolean, default=False)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    received_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    __table_args__ = (
        UniqueConstraint("provider", "event_key", name="uq_payment_events_key"),
    )


# =====================================================================
#  ПРОМОКОДЫ
# =====================================================================
class PromoCode(Base, IntPK, TimestampMixin):
    __tablename__ = "promo_codes"

    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    discount_type: Mapped[DiscountType] = mapped_column(
        _enum(DiscountType, "discount_type"), default=DiscountType.PERCENT
    )
    discount_value: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    max_uses: Mapped[int | None] = mapped_column(Integer)
    max_uses_per_user: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    used_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    min_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    valid_from: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    product_ids: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    category_ids: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    comment: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (CheckConstraint("discount_value > 0", name="promo_value_positive"),)


class PromoCodeUsage(Base, IntPK):
    __tablename__ = "promo_code_usages"

    promo_code_id: Mapped[int] = mapped_column(
        ForeignKey("promo_codes.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    amount_saved: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    used_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("promo_code_id", "order_id", name="uq_promo_usage_order"),
        Index("ix_promo_usage_user", "promo_code_id", "user_id"),
    )


# =====================================================================
#  CMS: ДИЗАЙН / ТЕКСТЫ / EMOJI / ИЗОБРАЖЕНИЯ / КНОПКИ
# =====================================================================
class DesignBlock(Base, IntPK, TimestampMixin):
    """Дизайн-конструктор экранов бота (ТЗ п.29, п.57)."""

    __tablename__ = "design_blocks"

    screen: Mapped[str] = mapped_column(String(64), index=True)  # main, catalog, ...
    block_type: Mapped[BlockType] = mapped_column(_enum(BlockType, "block_type"))
    position: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    title: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    content: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    emoji: Mapped[str | None] = mapped_column(String(16))
    image_url: Mapped[str | None] = mapped_column(Text)
    config: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)

    __table_args__ = (Index("ix_design_screen_pos", "screen", "position"),)


class DesignSetting(Base, IntPK, TimestampMixin):
    """Глобальные визуальные параметры (пресеты, ширина сетки кнопок и т.п.)."""

    __tablename__ = "design_settings"

    key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    group: Mapped[str] = mapped_column(String(64), default="general")
    label: Mapped[str | None] = mapped_column(String(255))


class TextSetting(Base, IntPK, TimestampMixin):
    """Все пользовательские тексты (ТЗ п.11, п.30). value = {"ru":..., "en":...}."""

    __tablename__ = "text_settings"

    key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    section: Mapped[str] = mapped_column(String(64), index=True, default="common")
    value: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    label: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    is_html: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class EmojiSetting(Base, IntPK, TimestampMixin):
    __tablename__ = "emoji_settings"

    key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    value: Mapped[str] = mapped_column(String(16))
    label: Mapped[str | None] = mapped_column(String(255))
    section: Mapped[str] = mapped_column(String(64), default="common")


class ImageSetting(Base, IntPK, TimestampMixin):
    __tablename__ = "image_settings"

    key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    url: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    label: Mapped[str | None] = mapped_column(String(255))
    purpose: Mapped[str | None] = mapped_column(String(255))
    # file_id телеграма — кэш после первой отправки, ускоряет ответы бота
    tg_file_id: Mapped[str | None] = mapped_column(String(255))


class ButtonSetting(Base, IntPK, TimestampMixin):
    """Кнопки интерфейса бота (ТЗ п.12, п.33)."""

    __tablename__ = "button_settings"

    screen: Mapped[str] = mapped_column(String(64), index=True)
    code: Mapped[str] = mapped_column(String(64))
    title: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    emoji: Mapped[str | None] = mapped_column(String(16))
    action: Mapped[ButtonAction] = mapped_column(
        _enum(ButtonAction, "button_action"), default=ButtonAction.NOOP
    )
    url: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[str | None] = mapped_column(String(128))
    row: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    position: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    style: Mapped[str] = mapped_column(String(32), default="default")
    is_wide: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    __table_args__ = (
        UniqueConstraint("screen", "code", name="uq_button_screen_code"),
        Index("ix_buttons_screen_layout", "screen", "is_active", "row", "position"),
    )


class SystemSetting(Base, IntPK, TimestampMixin):
    """Настройки магазина/админки/платежей (ТЗ п.34, п.35).

    Секретные значения хранятся зашифрованными (is_secret=True) и никогда
    не отдаются в шаблоны/API в открытом виде.
    """

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    group: Mapped[str] = mapped_column(String(64), default="general", index=True)
    label: Mapped[str | None] = mapped_column(String(255))
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


# =====================================================================
#  РАССЫЛКИ
# =====================================================================
class Broadcast(Base, IntPK, TimestampMixin):
    __tablename__ = "broadcasts"

    name: Mapped[str] = mapped_column(String(255))
    text: Mapped[str] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    buttons: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    audience: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    status: Mapped[BroadcastStatus] = mapped_column(
        _enum(BroadcastStatus, "broadcast_status"), default=BroadcastStatus.DRAFT, index=True
    )
    total: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    sent: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    failed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    blocked: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cursor_user_id: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("admins.id", ondelete="SET NULL")
    )


# =====================================================================
#  АУДИТ
# =====================================================================
class AuditLog(Base, IntPK):
    """Кто / что / где / когда / было → стало (ТЗ п.38)."""

    __tablename__ = "audit_logs"

    admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("admins.id", ondelete="SET NULL"), index=True
    )
    admin_login: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64), index=True)  # create/update/delete
    entity: Mapped[str] = mapped_column(String(64), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(64))
    summary: Mapped[str | None] = mapped_column(Text)
    old_value: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    new_value: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    __table_args__ = (Index("ix_audit_entity_created", "entity", "created_at"),)
