"""Подключение админ-панели к FastAPI-приложению."""
from __future__ import annotations

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.admin import templating
from app.admin.deps import AdminRedirect
from app.admin.routes import build_router
from app.core.config import settings

log = structlog.get_logger(__name__)


def include_admin(app: FastAPI) -> None:
    """Сессии, статика, роутеры и обработчики ошибок админки."""
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="tgshop_admin",
        max_age=settings.admin_session_max_age,
        same_site="lax",
        https_only=settings.is_production,
    )

    templating.STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount(
        f"{settings.admin_path.rstrip('/')}/static",
        StaticFiles(directory=str(templating.STATIC_DIR)),
        name="admin-static",
    )

    @app.exception_handler(AdminRedirect)
    async def _admin_redirect_handler(
        request: Request, exc: AdminRedirect
    ) -> RedirectResponse:
        return RedirectResponse(url=exc.url, status_code=303)

    app.include_router(build_router())
    log.info("admin.mounted", path=settings.admin_path)
