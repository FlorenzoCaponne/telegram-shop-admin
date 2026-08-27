"""Вход и выход администраторов с защитой от брута."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select

from app.admin import deps
from app.admin.templating import flash, render
from app.core.security import hash_password, needs_rehash, verify_password
from app.models import Admin
from app.services import audit

log = structlog.get_logger(__name__)
router = APIRouter(tags=["admin-auth"])

MAX_ATTEMPTS = 5
LOCK_MINUTES = 10


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, db: deps.Db) -> HTMLResponse:
    if request.session.get(deps.SESSION_ADMIN_KEY):
        admin = await deps.load_admin(request, db)
        if admin is not None:
            return RedirectResponse(url=deps.admin_url(), status_code=303)
    deps.ensure_csrf(request)
    next_path = request.query_params.get("next", "")
    return await render(
        request, "login.html", {"next_path": next_path, "error": None}, db=db
    )


@router.post("/login")
async def login_submit(request: Request, db: deps.Db):
    await deps.verify_csrf(request)
    form = await request.form()
    login = deps.form_str(form, "login").lower()
    password = deps.form_str(form, "password")
    next_path = deps.form_str(form, "next_path") or deps.admin_url()

    admin = await db.scalar(
        select(Admin).where(func.lower(Admin.login) == login)
    )
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    if admin is None or not admin.is_active:
        log.warning("admin.login_failed", login=login, reason="unknown")
        return await render(
            request,
            "login.html",
            {"next_path": next_path, "error": "Неверный логин или пароль"},
            status_code=401,
            db=db,
        )

    if admin.locked_until and admin.locked_until > now:
        left = int((admin.locked_until - now).total_seconds() // 60) + 1
        return await render(
            request,
            "login.html",
            {
                "next_path": next_path,
                "error": f"Слишком много попыток. Повторите через {left} мин.",
            },
            status_code=429,
            db=db,
        )

    if not verify_password(password, admin.password_hash):
        admin.failed_attempts = int(admin.failed_attempts or 0) + 1
        if admin.failed_attempts >= MAX_ATTEMPTS:
            admin.locked_until = now + timedelta(minutes=LOCK_MINUTES)
            admin.failed_attempts = 0
        await db.commit()
        log.warning("admin.login_failed", login=login, reason="password")
        return await render(
            request,
            "login.html",
            {"next_path": next_path, "error": "Неверный логин или пароль"},
            status_code=401,
            db=db,
        )

    if needs_rehash(admin.password_hash):
        admin.password_hash = hash_password(password)
    admin.failed_attempts = 0
    admin.locked_until = None
    admin.last_login_at = now
    await db.commit()

    request.session.clear()
    request.session[deps.SESSION_ADMIN_KEY] = admin.id
    deps.ensure_csrf(request)

    await audit.record(
        db,
        admin_id=admin.id,
        admin_login=admin.login,
        action="login",
        entity="admin",
        entity_id=admin.id,
        summary="Вход в админ-панель",
        ip=deps.client_ip(request),
        user_agent=deps.user_agent(request),
    )
    log.info("admin.login_ok", login=admin.login)
    if not next_path.startswith(deps.admin_url()):
        next_path = deps.admin_url()
    return deps.redirect(next_path)


@router.post("/logout")
async def logout(request: Request, db: deps.Db, admin: deps.CurrentAdmin):
    await deps.verify_csrf(request)
    await audit.record(
        db,
        admin_id=admin.id,
        admin_login=admin.login,
        action="logout",
        entity="admin",
        entity_id=admin.id,
        summary="Выход из админ-панели",
        ip=deps.client_ip(request),
        user_agent=deps.user_agent(request),
    )
    request.session.clear()
    return deps.redirect(deps.admin_url("login"))
