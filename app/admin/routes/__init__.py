"""Сборка всех роутеров админки под общим префиксом."""
from __future__ import annotations

from fastapi import APIRouter

from app.core.config import settings


def build_router() -> APIRouter:
    from app.admin.routes import (
        admins,
        auth,
        broadcast,
        catalog,
        content,
        dashboard,
        design,
        logs,
        orders,
        payments,
        promo,
        settings as settings_routes,
        users,
    )

    router = APIRouter(prefix=settings.admin_path.rstrip("/"))
    router.include_router(auth.router)
    router.include_router(dashboard.router)
    router.include_router(catalog.router)
    router.include_router(orders.router)
    router.include_router(payments.router)
    router.include_router(promo.router)
    router.include_router(users.router)
    router.include_router(broadcast.router)
    router.include_router(content.router)
    router.include_router(design.router)
    router.include_router(settings_routes.router)
    router.include_router(admins.router)
    router.include_router(logs.router)
    return router
