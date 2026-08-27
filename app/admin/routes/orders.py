"""Заказы: список, карточка, ручная выдача, повторная проверка оплаты (ТЗ п.35)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.admin import deps
from app.admin.templating import flash, render
from app.core.security import Perm
from app.models import OrderStatus, Payment, User
from app.services import audit, orders as orders_service, payments as payments_service

router = APIRouter(tags=["admin-orders"])

OrdersAdmin = Depends(deps.require_perm(Perm.orders))
PER_PAGE = 25


@router.get("/orders", response_class=HTMLResponse)
async def orders_list(request: Request, db: deps.Db, admin=OrdersAdmin) -> HTMLResponse:
    page, per_page, offset = deps.page_params(request, PER_PAGE)
    status = request.query_params.get("status", "").strip() or None
    query = request.query_params.get("q", "").strip() or None

    rows, total = await orders_service.list_orders(
        db, status=status, query=query, limit=per_page, offset=offset
    )
    return await render(
        request,
        "orders/list.html",
        {
            "rows": rows,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": max(1, (total + per_page - 1) // per_page),
            "status": status or "",
            "query": query or "",
            "statuses": [item.value for item in OrderStatus],
        },
        db=db,
    )


@router.get("/orders/{order_id}", response_class=HTMLResponse)
async def order_detail(
    order_id: int, request: Request, db: deps.Db, admin=OrdersAdmin
) -> HTMLResponse:
    order = await orders_service.get_order(db, order_id)
    if order is None:
        flash(request, "Заказ не найден", "err")
        return deps.redirect(deps.admin_url("orders"))

    payments = (
        (
            await db.execute(
                select(Payment)
                .where(Payment.order_id == order.id)
                .order_by(Payment.id.desc())
            )
        )
        .scalars()
        .unique()
        .all()
    )
    buyer = await db.get(User, order.user_id) if order.user_id else None
    return await render(
        request,
        "orders/detail.html",
        {"order": order, "payments": payments, "buyer": buyer},
        db=db,
    )


@router.post("/orders/{order_id}/deliver")
async def order_deliver(
    order_id: int, request: Request, db: deps.Db, admin=OrdersAdmin
):
    """Автовыдача вручную или выдача указанного содержимого."""
    await deps.verify_csrf(request)
    form = await request.form()
    order = await orders_service.get_order(db, order_id)
    if order is None:
        flash(request, "Заказ не найден", "err")
        return deps.redirect(deps.admin_url("orders"))

    content = deps.form_str(form, "content")
    if content:
        await orders_service.deliver_manually(db, order, content)
        summary = "Ручная выдача"
        flash(request, "Заказ выдан вручную")
    else:
        result = await orders_service.deliver(db, order)
        summary = f"Автовыдача: ok={result.ok} reason={result.reason}"
        flash(
            request,
            "Товар выдан" if result.ok else f"Не выдано: {result.reason}",
            "ok" if result.ok else "err",
        )

    await audit.record(
        db,
        admin_id=admin.id,
        admin_login=admin.login,
        action="deliver",
        entity="order",
        entity_id=order.id,
        summary=summary,
        ip=deps.client_ip(request),
        user_agent=deps.user_agent(request),
    )
    return deps.redirect(deps.admin_url("orders", str(order_id)))


@router.post("/orders/{order_id}/sync")
async def order_sync(order_id: int, request: Request, db: deps.Db, admin=OrdersAdmin):
    """Принудительно спросить статус у платёжки."""
    await deps.verify_csrf(request)
    order = await orders_service.get_order(db, order_id)
    if order is None:
        flash(request, "Заказ не найден", "err")
        return deps.redirect(deps.admin_url("orders"))

    result = await payments_service.sync_order_payment(db, order)
    if result.error:
        flash(request, f"Ответ провайдера: {result.error}", "err")
    else:
        flash(request, f"Статус платежа: {result.status}")
    if result.just_paid:
        delivery = await orders_service.deliver(db, order)
        flash(
            request,
            "Оплата подтверждена, товар выдан"
            if delivery.ok
            else f"Оплата подтверждена, выдача требует внимания: {delivery.reason}",
            "ok" if delivery.ok else "err",
        )
    return deps.redirect(deps.admin_url("orders", str(order_id)))


@router.post("/orders/{order_id}/cancel")
async def order_cancel(order_id: int, request: Request, db: deps.Db, admin=OrdersAdmin):
    await deps.verify_csrf(request)
    form = await request.form()
    order = await orders_service.get_order(db, order_id)
    if order is None:
        flash(request, "Заказ не найден", "err")
        return deps.redirect(deps.admin_url("orders"))

    reason = deps.form_str(form, "reason", "Отменено администратором")
    await orders_service.cancel_order(db, order, reason)
    await audit.record(
        db,
        admin_id=admin.id,
        admin_login=admin.login,
        action="cancel",
        entity="order",
        entity_id=order.id,
        summary=reason,
        ip=deps.client_ip(request),
        user_agent=deps.user_agent(request),
    )
    flash(request, "Заказ отменён")
    return deps.redirect(deps.admin_url("orders", str(order_id)))
