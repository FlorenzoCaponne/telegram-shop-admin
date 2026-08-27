"""Промокоды в админке (ТЗ п.37)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.admin import deps
from app.admin.templating import flash, render
from app.core.security import Perm
from app.models import DiscountType
from app.services import audit, catalog, promo as promo_service

router = APIRouter(tags=["admin-promo"])

PromoAdmin = Depends(deps.require_perm(Perm.promo))
PER_PAGE = 50


@router.get("/promo", response_class=HTMLResponse)
async def promo_list(request: Request, db: deps.Db, admin=PromoAdmin) -> HTMLResponse:
    page, per_page, offset = deps.page_params(request, PER_PAGE)
    query = request.query_params.get("q", "").strip() or None
    rows, total = await promo_service.list_promos(
        db, query=query, limit=per_page, offset=offset
    )
    stats = {row.id: await promo_service.usage_stats(db, row.id) for row in rows}
    return await render(
        request,
        "promo/list.html",
        {
            "rows": rows,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": max(1, (total + per_page - 1) // per_page),
            "query": query or "",
            "stats": stats,
        },
        db=db,
    )


@router.get("/promo/new", response_class=HTMLResponse)
async def promo_new(request: Request, db: deps.Db, admin=PromoAdmin) -> HTMLResponse:
    return await render(
        request,
        "promo/form.html",
        {
            "promo": None,
            "discount_types": [item.value for item in DiscountType],
            "products": await catalog.product_choices(db),
            "categories": await catalog.category_choices(db),
            "stats": None,
        },
        db=db,
    )


@router.get("/promo/{promo_id}", response_class=HTMLResponse)
async def promo_edit(
    promo_id: int, request: Request, db: deps.Db, admin=PromoAdmin
) -> HTMLResponse:
    promo = await promo_service.get_promo(db, promo_id)
    if promo is None:
        flash(request, "Промокод не найден", "err")
        return deps.redirect(deps.admin_url("promo"))
    return await render(
        request,
        "promo/form.html",
        {
            "promo": promo,
            "discount_types": [item.value for item in DiscountType],
            "products": await catalog.product_choices(db),
            "categories": await catalog.category_choices(db),
            "stats": await promo_service.usage_stats(db, promo.id),
        },
        db=db,
    )


@router.post("/promo/save")
async def promo_save(request: Request, db: deps.Db, admin=PromoAdmin):
    await deps.verify_csrf(request)
    form = await request.form()
    promo_id = deps.form_int(form, "id")

    data = {
        "code": deps.form_str(form, "code"),
        "discount_type": deps.form_str(form, "discount_type", "percent"),
        "discount_value": deps.form_str(form, "discount_value", "1"),
        "max_uses": deps.form_str(form, "max_uses"),
        "max_uses_per_user": deps.form_int(form, "max_uses_per_user", 1),
        "min_amount": deps.form_str(form, "min_amount"),
        "valid_from": deps.form_str(form, "valid_from"),
        "valid_until": deps.form_str(form, "valid_until"),
        "product_ids": [
            int(value) for value in form.getlist("product_ids") if str(value).isdigit()
        ],
        "category_ids": [
            int(value) for value in form.getlist("category_ids") if str(value).isdigit()
        ],
        "is_active": deps.form_bool(form, "is_active"),
        "comment": deps.form_str(form, "comment"),
    }
    if not data["code"]:
        flash(request, "Код обязателен", "err")
        return deps.redirect(deps.admin_url("promo"))

    promo = await promo_service.save_promo(db, promo_id=promo_id, data=data)
    await audit.record(
        db,
        admin_id=admin.id,
        admin_login=admin.login,
        action="save",
        entity="promo",
        entity_id=promo.id,
        summary=f"Промокод {promo.code}",
        ip=deps.client_ip(request),
        user_agent=deps.user_agent(request),
    )
    flash(request, "Промокод сохранён")
    return deps.redirect(deps.admin_url("promo"))


@router.post("/promo/{promo_id}/delete")
async def promo_delete(promo_id: int, request: Request, db: deps.Db, admin=PromoAdmin):
    await deps.verify_csrf(request)
    ok = await promo_service.delete_promo(db, promo_id)
    if ok:
        await audit.record(
            db,
            admin_id=admin.id,
            admin_login=admin.login,
            action="delete",
            entity="promo",
            entity_id=promo_id,
            summary="Удаление промокода",
            ip=deps.client_ip(request),
            user_agent=deps.user_agent(request),
        )
    flash(request, "Удалено" if ok else "Не найдено", "ok" if ok else "err")
    return deps.redirect(deps.admin_url("promo"))


@router.post("/promo/{promo_id}/toggle")
async def promo_toggle(promo_id: int, request: Request, db: deps.Db, admin=PromoAdmin):
    await deps.verify_csrf(request)
    promo = await promo_service.toggle_promo(db, promo_id)
    if promo is not None:
        flash(request, f"Промокод {promo.code}: активен = {promo.is_active}")
    return deps.redirect(deps.admin_url("promo"))
