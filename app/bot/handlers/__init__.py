"""Сборка роутеров бота. Порядок важен: misc содержит catch-all и идёт последним."""
from __future__ import annotations

from aiogram import Router

from app.bot.handlers import catalog, misc, orders, purchase, start


def get_router() -> Router:
    router = Router(name="root")
    router.include_router(start.router)
    router.include_router(catalog.router)
    router.include_router(purchase.router)
    router.include_router(orders.router)
    router.include_router(misc.router)
    return router


__all__ = ["get_router", "start", "catalog", "purchase", "orders", "misc"]
