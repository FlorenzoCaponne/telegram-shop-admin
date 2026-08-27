"""Зависимости админки: сессия, права, CSRF, редиректы."""
from __future__ import annotations

import secrets
from typing import Annotated, Any

import structlog
from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import Perm, csrf_ok, role_has
from app.db.session import get_db as _get_db
from app.models import Admin

log = structlog.get_logger(__name__)

SESSION_ADMIN_KEY = "admin_id"
SESSION_CSRF_KEY = "csrf"
SESSION_NEXT_KEY = "next"


class AdminRedirect(Exception):
    """Перехватывается обработчиком и превращается в 303-редирект."""

    def __init__(self, url: str) -> None:
        self.url = url
        super().__init__(url)


async def get_db() -> Any:
    async for session in _get_db():
        yield session


Db = Annotated[AsyncSession, Depends(get_db)]


def admin_url(*parts: str) -> str:
    """Собрать путь внутри админки с учётом настраиваемого префикса."""
    base = settings.admin_path.rstrip("/")
    tail = "/".join(str(p).strip("/") for p in parts if str(p).strip("/"))
    return f"{base}/{tail}" if tail else base or "/"


def login_url(next_path: str | None = None) -> str:
    url = admin_url("login")
    if next_path:
        from urllib.parse import quote

        return f"{url}?next={quote(next_path, safe='')}"
    return url


def redirect(path: str, status_code: int = 303) -> RedirectResponse:
    return RedirectResponse(url=path, status_code=status_code)


def ensure_csrf(request: Request) -> str:
    """Выдать (или создать) CSRF-токен сессии."""
    token = request.session.get(SESSION_CSRF_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[SESSION_CSRF_KEY] = token
    return str(token)


async def verify_csrf(request: Request) -> None:
    """Проверка CSRF для любого POST в админке."""
    expected = str(request.session.get(SESSION_CSRF_KEY) or "")
    form = await request.form()
    provided = str(form.get("csrf_token") or request.headers.get("X-CSRF-Token") or "")
    if not expected or not csrf_ok(expected, provided):
        log.warning("admin.csrf_failed", path=request.url.path)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="CSRF token invalid"
        )


Csrf = Annotated[None, Depends(verify_csrf)]


async def load_admin(request: Request, db: AsyncSession) -> Admin | None:
    admin_id = request.session.get(SESSION_ADMIN_KEY)
    if not admin_id:
        return None
    admin = await db.scalar(select(Admin).where(Admin.id == int(admin_id)))
    if admin is None or not admin.is_active:
        request.session.pop(SESSION_ADMIN_KEY, None)
        return None
    return admin


async def current_admin(request: Request, db: Db) -> Admin:
    """Текущий админ или редирект на форму входа."""
    admin = await load_admin(request, db)
    if admin is None:
        raise AdminRedirect(login_url(request.url.path))
    request.state.admin = admin
    ensure_csrf(request)
    return admin


CurrentAdmin = Annotated[Admin, Depends(current_admin)]


def require_perm(perm: Perm):
    """Зависимость: админ с конкретным правом."""

    async def dependency(admin: CurrentAdmin) -> Admin:
        if not role_has(admin.role, perm):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Недостаточно прав"
            )
        return admin

    return dependency


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def user_agent(request: Request) -> str:
    return request.headers.get("User-Agent", "")[:500]


def form_bool(form: Any, key: str) -> bool:
    """Чекбокс из HTML-формы: отсутствует = False."""
    value = form.get(key)
    if value is None:
        return False
    return str(value).lower() in {"1", "on", "true", "yes", "да"}


def form_int(form: Any, key: str, default: int | None = None) -> int | None:
    raw = str(form.get(key) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def form_str(form: Any, key: str, default: str = "") -> str:
    value = form.get(key)
    return str(value).strip() if value is not None else default


def page_params(request: Request, per_page: int = 25) -> tuple[int, int, int]:
    """(page, per_page, offset) из query-параметров."""
    try:
        page = max(1, int(request.query_params.get("page", 1)))
    except ValueError:
        page = 1
    return page, per_page, (page - 1) * per_page
