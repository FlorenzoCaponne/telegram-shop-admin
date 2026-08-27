"""Промокоды (ТЗ п.26): процент/фикс, лимиты, сроки, привязка к товарам."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Sequence

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    DiscountType,
    Order,
    Product,
    PromoCode,
    PromoCodeUsage,
    User,
)

log = structlog.get_logger(__name__)

REASON_NOT_FOUND = "not_found"
REASON_INACTIVE = "inactive"
REASON_EXPIRED = "expired"
REASON_NOT_STARTED = "not_started"
REASON_LIMIT = "limit_reached"
REASON_USER_LIMIT = "user_limit_reached"
REASON_MIN_AMOUNT = "min_amount"
REASON_NOT_APPLICABLE = "not_applicable"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _decimal(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value).replace(",", ".").strip())
    except (InvalidOperation, AttributeError, TypeError, ValueError):
        return Decimal(default)


def normalize_code(code: str) -> str:
    return (code or "").strip().upper()[:64]


@dataclass(slots=True)
class PromoResult:
    ok: bool
    promo: PromoCode | None = None
    discount: Decimal = Decimal("0")
    reason: str | None = None


def compute_discount(promo: PromoCode, amount: Decimal) -> Decimal:
    amount = Decimal(amount)
    if promo.discount_type == DiscountType.PERCENT:
        discount = amount * Decimal(promo.discount_value) / Decimal("100")
    else:
        discount = Decimal(promo.discount_value)
    discount = discount.quantize(Decimal("0.01"))
    if discount > amount:
        discount = amount
    if discount < 0:
        discount = Decimal("0")
    return discount


async def get_by_code(db: AsyncSession, code: str) -> PromoCode | None:
    return (
        await db.execute(select(PromoCode).where(PromoCode.code == normalize_code(code)))
    ).scalar_one_or_none()


async def validate(
    db: AsyncSession,
    *,
    code: str,
    user: User,
    product: Product | None = None,
    amount: Decimal | None = None,
) -> PromoResult:
    promo = await get_by_code(db, code)
    if promo is None:
        return PromoResult(ok=False, reason=REASON_NOT_FOUND)
    if not promo.is_active:
        return PromoResult(ok=False, promo=promo, reason=REASON_INACTIVE)

    now = _now()
    if promo.valid_from and promo.valid_from > now:
        return PromoResult(ok=False, promo=promo, reason=REASON_NOT_STARTED)
    if promo.valid_until and promo.valid_until < now:
        return PromoResult(ok=False, promo=promo, reason=REASON_EXPIRED)
    if promo.max_uses is not None and int(promo.used_count or 0) >= int(promo.max_uses):
        return PromoResult(ok=False, promo=promo, reason=REASON_LIMIT)

    per_user = int(promo.max_uses_per_user or 1)
    if per_user > 0:
        used_stmt = select(func.count(PromoCodeUsage.id)).where(
            PromoCodeUsage.promo_code_id == promo.id,
            PromoCodeUsage.user_id == user.id,
        )
        used = int((await db.execute(used_stmt)).scalar() or 0)
        if used >= per_user:
            return PromoResult(ok=False, promo=promo, reason=REASON_USER_LIMIT)

    total = Decimal(amount if amount is not None else (product.price if product else 0))
    if promo.min_amount is not None and total < Decimal(promo.min_amount):
        return PromoResult(ok=False, promo=promo, reason=REASON_MIN_AMOUNT)

    if product is not None:
        product_ids = [int(x) for x in (promo.product_ids or [])]
        category_ids = [int(x) for x in (promo.category_ids or [])]
        if product_ids and product.id not in product_ids:
            return PromoResult(ok=False, promo=promo, reason=REASON_NOT_APPLICABLE)
        if category_ids and (product.category_id or 0) not in category_ids:
            return PromoResult(ok=False, promo=promo, reason=REASON_NOT_APPLICABLE)

    return PromoResult(ok=True, promo=promo, discount=compute_discount(promo, total))


async def register_usage(
    db: AsyncSession, *, promo: PromoCode, user_id: int, order: Order
) -> None:
    """Фиксируется только после успешной оплаты заказа."""
    exists = (
        await db.execute(
            select(PromoCodeUsage.id).where(
                PromoCodeUsage.promo_code_id == promo.id,
                PromoCodeUsage.order_id == order.id,
            )
        )
    ).scalar_one_or_none()
    if exists:
        return
    db.add(
        PromoCodeUsage(
            promo_code_id=promo.id,
            user_id=user_id,
            order_id=order.id,
            amount_saved=Decimal(order.discount_amount or 0),
        )
    )
    promo.used_count = int(promo.used_count or 0) + 1
    await db.commit()
    log.info("promo.used", code=promo.code, order_no=order.public_no)


async def register_usage_for_order(db: AsyncSession, order: Order) -> None:
    if not order.promo_code_id:
        return
    promo = await db.get(PromoCode, order.promo_code_id)
    if promo is None:
        return
    await register_usage(db, promo=promo, user_id=order.user_id, order=order)


# =====================================================================
#  АДМИНКА
# =====================================================================
async def list_promos(
    db: AsyncSession,
    *,
    query: str | None = None,
    only_active: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> tuple[Sequence[PromoCode], int]:
    stmt = select(PromoCode)
    count_stmt = select(func.count(PromoCode.id))
    conditions = []
    if query:
        conditions.append(PromoCode.code.ilike(f"%{query.strip().upper()}%"))
    if only_active:
        conditions.append(PromoCode.is_active.is_(True))
    for condition in conditions:
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    stmt = stmt.order_by(PromoCode.id.desc()).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().unique().all()
    total = int((await db.execute(count_stmt)).scalar() or 0)
    return rows, total


async def get_promo(db: AsyncSession, promo_id: int) -> PromoCode | None:
    return await db.get(PromoCode, promo_id)


def _parse_dt(value: Any) -> dt.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


async def save_promo(
    db: AsyncSession, *, promo_id: int | None, data: dict[str, Any]
) -> PromoCode:
    promo = await db.get(PromoCode, promo_id) if promo_id else None
    if promo is None:
        promo = PromoCode(code="", discount_value=Decimal("1"))
        db.add(promo)

    promo.code = normalize_code(str(data.get("code") or ""))
    promo.discount_type = DiscountType(str(data.get("discount_type") or "percent"))
    promo.discount_value = _decimal(data.get("discount_value"), "1")
    promo.max_uses = int(data["max_uses"]) if str(data.get("max_uses") or "").strip() else None
    promo.max_uses_per_user = max(0, int(data.get("max_uses_per_user") or 1))
    promo.min_amount = (
        _decimal(data["min_amount"]) if str(data.get("min_amount") or "").strip() else None
    )
    promo.valid_from = _parse_dt(data.get("valid_from"))
    promo.valid_until = _parse_dt(data.get("valid_until"))
    promo.product_ids = [int(x) for x in (data.get("product_ids") or [])]
    promo.category_ids = [int(x) for x in (data.get("category_ids") or [])]
    promo.is_active = bool(data.get("is_active", True))
    promo.comment = data.get("comment") or None

    await db.commit()
    return promo


async def delete_promo(db: AsyncSession, promo_id: int) -> bool:
    promo = await db.get(PromoCode, promo_id)
    if promo is None:
        return False
    await db.delete(promo)
    await db.commit()
    return True


async def toggle_promo(db: AsyncSession, promo_id: int) -> PromoCode | None:
    promo = await db.get(PromoCode, promo_id)
    if promo is None:
        return None
    promo.is_active = not promo.is_active
    await db.commit()
    return promo


async def usage_stats(db: AsyncSession, promo_id: int) -> dict[str, Any]:
    stmt = select(
        func.count(PromoCodeUsage.id), func.coalesce(func.sum(PromoCodeUsage.amount_saved), 0)
    ).where(PromoCodeUsage.promo_code_id == promo_id)
    row = (await db.execute(stmt)).one()
    return {"uses": int(row[0] or 0), "saved": Decimal(row[1] or 0)}
