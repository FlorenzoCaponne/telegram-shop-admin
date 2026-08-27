"""Платежи: списки, события вебхуков, экспорт Excel (ТЗ п.36)."""
from __future__ import annotations

import datetime as dt

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.admin import deps
from app.admin.templating import flash, render
from app.core.config import settings
from app.core.security import Perm
from app.models import PaymentStatus
from app.payments import base as pay_base
from app.services import audit, payments as payments_service

log = structlog.get_logger(__name__)
router = APIRouter(tags=["admin-payments"])

PaymentsViewer = Depends(deps.require_perm(Perm.payments_view))
PER_PAGE = 50


@router.get("/payments", response_class=HTMLResponse)
async def payments_list(
    request: Request, db: deps.Db, admin=PaymentsViewer
) -> HTMLResponse:
    page, per_page, offset = deps.page_params(request, PER_PAGE)
    status = request.query_params.get("status", "").strip() or None
    query = request.query_params.get("q", "").strip() or None

    rows, total = await payments_service.list_payments(
        db, status=status, query=query, limit=per_page, offset=offset
    )
    return await render(
        request,
        "payments/list.html",
        {
            "rows": rows,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": max(1, (total + per_page - 1) // per_page),
            "status": status or "",
            "query": query or "",
            "statuses": [item.value for item in PaymentStatus],
            "summary": await payments_service.payments_summary(db, days=30),
            "provider": await payments_service.provider_status(db),
            "methods": pay_base.METHODS,
            "export_statuses": list(pay_base.EXPORT_STATUS_CODES.keys()),
        },
        db=db,
    )


@router.get("/payments/events", response_class=HTMLResponse)
async def payment_events(
    request: Request, db: deps.Db, admin=PaymentsViewer
) -> HTMLResponse:
    page, per_page, offset = deps.page_params(request, 100)
    rows, total = await payments_service.list_events(db, limit=per_page, offset=offset)
    return await render(
        request,
        "payments/events.html",
        {
            "rows": rows,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": max(1, (total + per_page - 1) // per_page),
        },
        db=db,
    )


@router.post("/payments/{payment_id}/sync")
async def payment_sync(
    payment_id: int, request: Request, db: deps.Db, admin=PaymentsViewer
):
    """Переспросить статус конкретного платежа у провайдера."""
    await deps.verify_csrf(request)
    from app.models import Payment

    payment = await db.get(Payment, payment_id)
    if payment is None:
        flash(request, "Платёж не найден", "err")
        return deps.redirect(deps.admin_url("payments"))

    result = await payments_service.sync_payment(db, payment)
    flash(
        request,
        f"Статус: {result.status}" if not result.error else f"Ошибка: {result.error}",
        "ok" if not result.error else "err",
    )
    if result.just_paid:
        from app.services import orders as orders_service

        order = await orders_service.get_order(db, payment.order_id)
        if order is not None:
            await orders_service.deliver(db, order)
            flash(request, "Оплата подтверждена, выполнена выдача")
    return deps.redirect(deps.admin_url("payments"))


@router.post("/payments/export")
async def payments_export(request: Request, db: deps.Db, admin=PaymentsViewer):
    """Выгрузка транзакций у провайдера — возвращает ссылку на файл."""
    await deps.verify_csrf(request)
    form = await request.form()

    statuses = [str(value) for value in form.getlist("statuses")] or [
        pay_base.PROVIDER_STATUS_CONFIRMED
    ]
    methods = [
        int(value) for value in form.getlist("payment_methods") if str(value).isdigit()
    ] or list(pay_base.DEFAULT_METHODS)

    now = dt.datetime.now(dt.timezone.utc)
    raw_from = deps.form_str(form, "date_from")
    raw_to = deps.form_str(form, "date_to")
    try:
        date_from = dt.datetime.fromisoformat(raw_from) if raw_from else now - dt.timedelta(days=30)
        date_to = dt.datetime.fromisoformat(raw_to) if raw_to else now
    except ValueError:
        flash(request, "Неверный формат дат", "err")
        return deps.redirect(deps.admin_url("payments"))

    try:
        result = await payments_service.export_transactions_excel(
            db,
            statuses=statuses,
            payment_methods=methods,
            date_from=date_from,
            date_to=date_to,
            timezone_id=settings.timezone,
        )
    except pay_base.PaymentProviderError as exc:
        log.warning("admin.export_failed", error=str(exc))
        flash(request, f"Провайдер отказал: {exc}", "err")
        return deps.redirect(deps.admin_url("payments"))

    await audit.record(
        db,
        admin_id=admin.id,
        admin_login=admin.login,
        action="export",
        entity="payment",
        entity_id=0,
        summary=f"Экспорт транзакций {date_from:%d.%m.%Y} — {date_to:%d.%m.%Y}",
        ip=deps.client_ip(request),
        user_agent=deps.user_agent(request),
    )
    if result.url:
        flash(request, f"Файл готов: {result.url}")
    else:
        flash(request, "Провайдер не вернул ссылку", "err")
    return deps.redirect(deps.admin_url("payments"))
