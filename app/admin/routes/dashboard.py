"""Главная страница админки: метрики, графики, алерты (ТЗ п.31)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.admin import deps
from app.admin.templating import render
from app.core.security import Perm
from app.services import payments as payments_service, stats

router = APIRouter(tags=["admin-dashboard"])

DashboardAdmin = Depends(deps.require_perm(Perm.dashboard))


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request, db: deps.Db, admin=DashboardAdmin
) -> HTMLResponse:
    overview = await stats.overview(db)
    context = {
        "overview": overview,
        "revenue_chart": await stats.revenue_chart(db, days=14),
        "users_chart": await stats.users_chart(db, days=14),
        "top_products": await stats.top_products(db, days=30, limit=8),
        "orders_by_status": await stats.orders_by_status(db),
        "payments_by_status": await stats.payments_by_status(db),
        "alerts": await stats.alerts(db),
        "recent_orders": await stats.recent_orders(db, limit=10),
        "provider": await payments_service.provider_status(db),
        "funnel": await stats.funnel(db, days=30),
    }
    return await render(request, "dashboard.html", context, db=db)


@router.get("/fragments/overview", response_class=HTMLResponse)
async def overview_fragment(
    request: Request, db: deps.Db, admin=DashboardAdmin
) -> HTMLResponse:
    """HTMX-фрагмент: автообновление карточек без перезагрузки страницы."""
    return await render(
        request,
        "fragments/overview_cards.html",
        {"overview": await stats.overview(db)},
        db=db,
    )
