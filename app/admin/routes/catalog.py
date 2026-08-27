"""Категории, товары и склад (ТЗ п.32-п.34).

Все многоязычные поля приходят из формы как title__ru / title__en.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.admin import deps
from app.admin.templating import flash, render
from app.core.config import settings
from app.core.security import Perm
from app.models import DeliveryType
from app.payments.base import METHODS
from app.services import audit, catalog, orders as orders_service

router = APIRouter(tags=["admin-catalog"])

CatalogAdmin = Depends(deps.require_perm(Perm.catalog))
InventoryAdmin = Depends(deps.require_perm(Perm.inventory))

PER_PAGE = 25


def _i18n_field(form: Any, prefix: str) -> dict[str, str]:
    """Собрать {"ru": ..., "en": ...} из полей prefix__<locale>."""
    result: dict[str, str] = {}
    for locale in settings.locales:
        value = str(form.get(f"{prefix}__{locale}") or "").strip()
        if value:
            result[locale] = value
    return result


async def _log(
    db, request: Request, admin, action: str, entity: str, entity_id: int, summary: str
) -> None:
    await audit.record(
        db,
        admin_id=admin.id,
        admin_login=admin.login,
        action=action,
        entity=entity,
        entity_id=entity_id,
        summary=summary,
        ip=deps.client_ip(request),
        user_agent=deps.user_agent(request),
    )


# =====================================================================
#  КАТЕГОРИИ
# =====================================================================
@router.get("/categories", response_class=HTMLResponse)
async def categories_list(
    request: Request, db: deps.Db, admin=CatalogAdmin
) -> HTMLResponse:
    query = request.query_params.get("q", "").strip() or None
    rows = await catalog.list_categories(db, query=query)
    return await render(
        request,
        "catalog/categories.html",
        {"rows": rows, "query": query or "", "counters": await catalog.counters(db)},
        db=db,
    )


@router.get("/categories/new", response_class=HTMLResponse)
async def category_new(request: Request, db: deps.Db, admin=CatalogAdmin) -> HTMLResponse:
    return await render(
        request, "catalog/category_form.html", {"category": None}, db=db
    )


@router.get("/categories/{category_id}", response_class=HTMLResponse)
async def category_edit(
    category_id: int, request: Request, db: deps.Db, admin=CatalogAdmin
) -> HTMLResponse:
    category = await catalog.get_category(db, category_id)
    if category is None:
        flash(request, "Категория не найдена", "err")
        return deps.redirect(deps.admin_url("categories"))
    return await render(
        request, "catalog/category_form.html", {"category": category}, db=db
    )


@router.post("/categories/save")
async def category_save(request: Request, db: deps.Db, admin=CatalogAdmin):
    await deps.verify_csrf(request)
    form = await request.form()
    category_id = deps.form_int(form, "id")
    data = {
        "slug": deps.form_str(form, "slug"),
        "title": _i18n_field(form, "title"),
        "description": _i18n_field(form, "description"),
        "emoji": deps.form_str(form, "emoji") or "📂",
        "image_url": deps.form_str(form, "image_url"),
        "sort_order": deps.form_int(form, "sort_order", 100),
        "is_active": deps.form_bool(form, "is_active"),
        "buttons_per_row": deps.form_int(form, "buttons_per_row", 2),
    }
    category = await catalog.save_category(db, category_id=category_id, data=data)
    await _log(
        db, request, admin, "save", "category", category.id, f"Категория {category.slug}"
    )
    flash(request, "Категория сохранена")
    return deps.redirect(deps.admin_url("categories"))


@router.post("/categories/{category_id}/delete")
async def category_delete(
    category_id: int, request: Request, db: deps.Db, admin=CatalogAdmin
):
    await deps.verify_csrf(request)
    ok = await catalog.delete_category(db, category_id)
    if ok:
        await _log(db, request, admin, "delete", "category", category_id, "Удаление категории")
    flash(request, "Категория удалена" if ok else "Не найдено", "ok" if ok else "err")
    return deps.redirect(deps.admin_url("categories"))


@router.post("/categories/{category_id}/toggle")
async def category_toggle(
    category_id: int, request: Request, db: deps.Db, admin=CatalogAdmin
):
    await deps.verify_csrf(request)
    category = await catalog.toggle_category(db, category_id)
    if category is not None:
        await _log(
            db,
            request,
            admin,
            "toggle",
            "category",
            category.id,
            f"Активность: {category.is_active}",
        )
    return deps.redirect(deps.admin_url("categories"))


@router.post("/categories/{category_id}/move")
async def category_move(
    category_id: int, request: Request, db: deps.Db, admin=CatalogAdmin
):
    await deps.verify_csrf(request)
    form = await request.form()
    await catalog.move_category(db, category_id, deps.form_str(form, "direction", "up"))
    return deps.redirect(deps.admin_url("categories"))


# =====================================================================
#  ТОВАРЫ
# =====================================================================
@router.get("/products", response_class=HTMLResponse)
async def products_list(
    request: Request, db: deps.Db, admin=CatalogAdmin
) -> HTMLResponse:
    page, per_page, offset = deps.page_params(request, PER_PAGE)
    query = request.query_params.get("q", "").strip() or None
    category_id = None
    raw_category = request.query_params.get("category_id", "").strip()
    if raw_category.isdigit():
        category_id = int(raw_category)

    rows, total = await catalog.list_products(
        db, query=query, category_id=category_id, limit=per_page, offset=offset
    )
    stock = await catalog.stock_map(db, [row.id for row in rows])
    return await render(
        request,
        "catalog/products.html",
        {
            "rows": rows,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": max(1, (total + per_page - 1) // per_page),
            "query": query or "",
            "category_id": category_id,
            "categories": await catalog.category_choices(db),
            "stock": stock,
        },
        db=db,
    )


@router.get("/products/new", response_class=HTMLResponse)
async def product_new(request: Request, db: deps.Db, admin=CatalogAdmin) -> HTMLResponse:
    return await render(
        request,
        "catalog/product_form.html",
        {
            "product": None,
            "categories": await catalog.category_choices(db),
            "delivery_types": [item.value for item in DeliveryType],
            "methods": METHODS,
            "stock": None,
        },
        db=db,
    )


@router.get("/products/{product_id}", response_class=HTMLResponse)
async def product_edit(
    product_id: int, request: Request, db: deps.Db, admin=CatalogAdmin
) -> HTMLResponse:
    product = await catalog.get_product(db, product_id)
    if product is None:
        flash(request, "Товар не найден", "err")
        return deps.redirect(deps.admin_url("products"))
    return await render(
        request,
        "catalog/product_form.html",
        {
            "product": product,
            "categories": await catalog.category_choices(db),
            "delivery_types": [item.value for item in DeliveryType],
            "methods": METHODS,
            "stock": await catalog.stock(db, product),
        },
        db=db,
    )


@router.post("/products/save")
async def product_save(request: Request, db: deps.Db, admin=CatalogAdmin):
    await deps.verify_csrf(request)
    form = await request.form()
    product_id = deps.form_int(form, "id")

    delivery_type = deps.form_str(form, "delivery_type", "static_text")
    delivery_payload: dict[str, Any] = {}
    if delivery_type == "static_text":
        delivery_payload = {"text": _i18n_field(form, "delivery_text")}

    methods = [
        int(value)
        for value in form.getlist("payment_methods")
        if str(value).strip().isdigit()
    ]

    data = {
        "slug": deps.form_str(form, "slug"),
        "category_id": deps.form_int(form, "category_id"),
        "title": _i18n_field(form, "title"),
        "description": _i18n_field(form, "description"),
        "buy_button_text": _i18n_field(form, "buy_button_text"),
        "emoji": deps.form_str(form, "emoji") or "🛍",
        "price": deps.form_str(form, "price", "0"),
        "old_price": deps.form_str(form, "old_price"),
        "currency": deps.form_str(form, "currency", "RUB"),
        "image_url": deps.form_str(form, "image_url"),
        "delivery_type": delivery_type,
        "delivery_payload": delivery_payload,
        "payment_methods": methods,
        "sort_order": deps.form_int(form, "sort_order", 100),
        "is_active": deps.form_bool(form, "is_active"),
    }
    product = await catalog.save_product(db, product_id=product_id, data=data)

    inventory_lines = deps.form_str(form, "inventory_lines")
    if inventory_lines:
        added = await orders_service.bulk_add_inventory(db, product.id, inventory_lines)
        flash(request, f"Добавлено на склад: {added}")

    await _log(db, request, admin, "save", "product", product.id, f"Товар {product.slug}")
    flash(request, "Товар сохранён")
    return deps.redirect(deps.admin_url("products"))


@router.post("/products/{product_id}/delete")
async def product_delete(
    product_id: int, request: Request, db: deps.Db, admin=CatalogAdmin
):
    await deps.verify_csrf(request)
    ok = await catalog.delete_product(db, product_id)
    if ok:
        await _log(db, request, admin, "delete", "product", product_id, "Удаление товара")
    flash(request, "Товар удалён" if ok else "Не найдено", "ok" if ok else "err")
    return deps.redirect(deps.admin_url("products"))


@router.post("/products/{product_id}/toggle")
async def product_toggle(
    product_id: int, request: Request, db: deps.Db, admin=CatalogAdmin
):
    await deps.verify_csrf(request)
    product = await catalog.toggle_product(db, product_id)
    if product is not None:
        await _log(
            db,
            request,
            admin,
            "toggle",
            "product",
            product.id,
            f"Активность: {product.is_active}",
        )
    return deps.redirect(deps.admin_url("products"))


# =====================================================================
#  СКЛАД
# =====================================================================
@router.get("/inventory", response_class=HTMLResponse)
async def inventory_list(
    request: Request, db: deps.Db, admin=InventoryAdmin
) -> HTMLResponse:
    page, per_page, offset = deps.page_params(request, 50)
    raw_product = request.query_params.get("product_id", "").strip()
    product_id = int(raw_product) if raw_product.isdigit() else None
    status = request.query_params.get("status", "").strip() or None

    rows, total = await orders_service.list_inventory(
        db, product_id=product_id, status=status, limit=per_page, offset=offset
    )
    return await render(
        request,
        "catalog/inventory.html",
        {
            "rows": rows,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": max(1, (total + per_page - 1) // per_page),
            "product_id": product_id,
            "status": status or "",
            "products": await catalog.product_choices(db),
            "stats": await orders_service.inventory_stats(db),
        },
        db=db,
    )


@router.post("/inventory/add")
async def inventory_add(request: Request, db: deps.Db, admin=InventoryAdmin):
    await deps.verify_csrf(request)
    form = await request.form()
    product_id = deps.form_int(form, "product_id")
    lines = deps.form_str(form, "lines")
    if not product_id or not lines:
        flash(request, "Укажите товар и содержимое", "err")
        return deps.redirect(deps.admin_url("inventory"))
    added = await orders_service.bulk_add_inventory(db, product_id, lines)
    await _log(
        db, request, admin, "add", "inventory", product_id, f"Добавлено {added} ед."
    )
    flash(request, f"Добавлено: {added}")
    return deps.redirect(deps.admin_url("inventory") + f"?product_id={product_id}")


@router.post("/inventory/{item_id}/delete")
async def inventory_delete(
    item_id: int, request: Request, db: deps.Db, admin=InventoryAdmin
):
    await deps.verify_csrf(request)
    ok = await orders_service.delete_inventory_item(db, item_id)
    if ok:
        await _log(db, request, admin, "delete", "inventory", item_id, "Удаление единицы")
        flash(request, "Удалено")
    else:
        flash(request, "Выданные единицы удалять нельзя", "err")
    return deps.redirect(deps.admin_url("inventory"))
