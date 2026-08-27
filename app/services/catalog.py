"""Каталог: категории, товары, остатки (ТЗ п.13-п.18)."""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Sequence

import structlog
from slugify import slugify
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import NS_CATALOG, cache
from app.models import (
    Category,
    DeliveryType,
    InventoryItem,
    InventoryStatus,
    Product,
)

log = structlog.get_logger(__name__)


# =====================================================================
#  ЧТЕНИЕ ДЛЯ БОТА (через кэш)
# =====================================================================
async def active_categories(db: AsyncSession) -> list[dict[str, Any]]:
    async def loader() -> list[dict[str, Any]]:
        stmt = (
            select(Category)
            .where(Category.is_active.is_(True))
            .order_by(Category.sort_order, Category.id)
        )
        rows = (await db.execute(stmt)).scalars().unique().all()
        return [
            {
                "id": row.id,
                "slug": row.slug,
                "title": row.title or {},
                "description": row.description or {},
                "emoji": row.emoji,
                "image_url": row.image_url,
                "buttons_per_row": row.buttons_per_row,
            }
            for row in rows
        ]

    return await cache.get_or_set(NS_CATALOG, "categories", loader, ttl=300)


async def active_products(db: AsyncSession, category_id: int) -> list[dict[str, Any]]:
    async def loader() -> list[dict[str, Any]]:
        stmt = (
            select(Product)
            .where(Product.category_id == category_id, Product.is_active.is_(True))
            .order_by(Product.sort_order, Product.id)
        )
        rows = (await db.execute(stmt)).scalars().unique().all()
        return [
            {
                "id": row.id,
                "slug": row.slug,
                "title": row.title or {},
                "emoji": row.emoji,
                "price": str(row.price),
                "currency": row.currency,
                "delivery_type": str(row.delivery_type),
            }
            for row in rows
        ]

    return await cache.get_or_set(NS_CATALOG, f"products:{category_id}", loader, ttl=300)


async def get_category(db: AsyncSession, category_id: int) -> Category | None:
    return await db.get(Category, category_id)


async def get_product(db: AsyncSession, product_id: int) -> Product | None:
    return await db.get(Product, product_id)


async def get_product_by_slug(db: AsyncSession, slug: str) -> Product | None:
    return (
        await db.execute(select(Product).where(Product.slug == slug))
    ).scalar_one_or_none()


async def stock(db: AsyncSession, product: Product) -> int | None:
    """Остаток: None = неограничен (текстовая/ручная выдача)."""
    if product.delivery_type != DeliveryType.INVENTORY:
        return None
    stmt = select(func.count(InventoryItem.id)).where(
        InventoryItem.product_id == product.id,
        InventoryItem.status == InventoryStatus.AVAILABLE,
    )
    return int((await db.execute(stmt)).scalar() or 0)


async def stock_map(db: AsyncSession, product_ids: Sequence[int]) -> dict[int, int]:
    if not product_ids:
        return {}
    stmt = (
        select(InventoryItem.product_id, func.count(InventoryItem.id))
        .where(
            InventoryItem.product_id.in_(list(product_ids)),
            InventoryItem.status == InventoryStatus.AVAILABLE,
        )
        .group_by(InventoryItem.product_id)
    )
    rows = (await db.execute(stmt)).all()
    return {int(pid): int(count) for pid, count in rows}


async def is_available(db: AsyncSession, product: Product) -> bool:
    if not product.is_active:
        return False
    left = await stock(db, product)
    return True if left is None else left > 0


# =====================================================================
#  АДМИНКА: КАТЕГОРИИ
# =====================================================================
def make_slug(value: str, fallback: str = "item") -> str:
    slug = slugify(value or "", max_length=80)
    return slug or fallback


async def list_categories(
    db: AsyncSession, *, query: str | None = None, include_inactive: bool = True
) -> Sequence[Category]:
    stmt = select(Category).order_by(Category.sort_order, Category.id)
    if not include_inactive:
        stmt = stmt.where(Category.is_active.is_(True))
    if query:
        stmt = stmt.where(Category.slug.ilike(f"%{query.strip()}%"))
    return (await db.execute(stmt)).scalars().unique().all()


async def save_category(
    db: AsyncSession, *, category_id: int | None, data: dict[str, Any]
) -> Category:
    category = await db.get(Category, category_id) if category_id else None
    if category is None:
        category = Category(slug="")
        db.add(category)

    title = data.get("title") or {}
    slug_source = data.get("slug") or title.get("ru") or title.get("en") or "category"
    category.slug = make_slug(str(slug_source), fallback=f"category-{category_id or ''}".strip("-"))
    category.title = title
    category.description = data.get("description") or {}
    category.emoji = (data.get("emoji") or "📂")[:16]
    category.image_url = data.get("image_url") or None
    category.sort_order = int(data.get("sort_order") or 100)
    category.is_active = bool(data.get("is_active", True))
    category.buttons_per_row = max(1, min(4, int(data.get("buttons_per_row") or 2)))

    await db.commit()
    await cache.bump(NS_CATALOG)
    return category


async def delete_category(db: AsyncSession, category_id: int) -> bool:
    category = await db.get(Category, category_id)
    if category is None:
        return False
    await db.delete(category)
    await db.commit()
    await cache.bump(NS_CATALOG)
    return True


async def toggle_category(db: AsyncSession, category_id: int) -> Category | None:
    category = await db.get(Category, category_id)
    if category is None:
        return None
    category.is_active = not category.is_active
    await db.commit()
    await cache.bump(NS_CATALOG)
    return category


# =====================================================================
#  АДМИНКА: ТОВАРЫ
# =====================================================================
async def list_products(
    db: AsyncSession,
    *,
    query: str | None = None,
    category_id: int | None = None,
    only_active: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> tuple[Sequence[Product], int]:
    stmt = select(Product)
    count_stmt = select(func.count(Product.id))
    conditions = []

    if category_id:
        conditions.append(Product.category_id == category_id)
    if only_active:
        conditions.append(Product.is_active.is_(True))
    if query:
        like = f"%{query.strip()}%"
        conditions.append(
            or_(Product.slug.ilike(like), func.cast(Product.title, func.text().type).ilike(like))
            if False
            else Product.slug.ilike(like)
        )

    for condition in conditions:
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)

    stmt = stmt.order_by(Product.sort_order, Product.id).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().unique().all()
    total = int((await db.execute(count_stmt)).scalar() or 0)
    return rows, total


async def save_product(
    db: AsyncSession, *, product_id: int | None, data: dict[str, Any]
) -> Product:
    product = await db.get(Product, product_id) if product_id else None
    if product is None:
        product = Product(slug="", price=Decimal("0"))
        db.add(product)

    title = data.get("title") or {}
    slug_source = data.get("slug") or title.get("ru") or title.get("en") or "product"
    product.slug = make_slug(str(slug_source), fallback=f"product-{product_id or ''}".strip("-"))
    product.category_id = int(data["category_id"]) if data.get("category_id") else None
    product.title = title
    product.description = data.get("description") or {}
    product.emoji = (data.get("emoji") or "🛍")[:16]
    product.price = Decimal(str(data.get("price") or "0"))
    product.old_price = (
        Decimal(str(data["old_price"])) if data.get("old_price") not in (None, "") else None
    )
    product.currency = (data.get("currency") or "RUB")[:8]
    product.image_url = data.get("image_url") or None
    product.buy_button_text = data.get("buy_button_text") or {}
    product.extra_blocks = data.get("extra_blocks") or []
    product.delivery_type = DeliveryType(str(data.get("delivery_type") or "static_text"))
    product.delivery_payload = data.get("delivery_payload") or {}
    product.payment_methods = [int(m) for m in (data.get("payment_methods") or [])]
    product.sort_order = int(data.get("sort_order") or 100)
    product.is_active = bool(data.get("is_active", True))

    await db.commit()
    await cache.bump(NS_CATALOG)
    return product


async def delete_product(db: AsyncSession, product_id: int) -> bool:
    product = await db.get(Product, product_id)
    if product is None:
        return False
    await db.delete(product)
    await db.commit()
    await cache.bump(NS_CATALOG)
    return True


async def toggle_product(db: AsyncSession, product_id: int) -> Product | None:
    product = await db.get(Product, product_id)
    if product is None:
        return None
    product.is_active = not product.is_active
    await db.commit()
    await cache.bump(NS_CATALOG)
    return product


async def category_choices(db: AsyncSession) -> list[dict[str, Any]]:
    rows = await list_categories(db)
    return [
        {
            "id": row.id,
            "title": (row.title or {}).get("ru") or (row.title or {}).get("en") or row.slug,
            "emoji": row.emoji,
        }
        for row in rows
    ]


async def counters(db: AsyncSession) -> dict[str, int]:
    categories = int((await db.execute(select(func.count(Category.id)))).scalar() or 0)
    products = int((await db.execute(select(func.count(Product.id)))).scalar() or 0)
    active = int(
        (
            await db.execute(select(func.count(Product.id)).where(Product.is_active.is_(True)))
        ).scalar()
        or 0
    )
    available = int(
        (
            await db.execute(
                select(func.count(InventoryItem.id)).where(
                    InventoryItem.status == InventoryStatus.AVAILABLE
                )
            )
        ).scalar()
        or 0
    )
    return {
        "categories": categories,
        "products": products,
        "products_active": active,
        "inventory_available": available,
    }
