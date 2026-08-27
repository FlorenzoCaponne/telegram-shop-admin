"""Пользователи бота в админке (ТЗ п.38)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.admin import deps
from app.admin.templating import flash, render
from app.core.config import settings
from app.core.security import Perm
from app.services import audit, users as users_service

router = APIRouter(tags=["admin-users"])

UsersAdmin = Depends(deps.require_perm(Perm.users))
PER_PAGE = 50


@router.get("/users", response_class=HTMLResponse)
async def users_list(request: Request, db: deps.Db, admin=UsersAdmin) -> HTMLResponse:
    page, per_page, offset = deps.page_params(request, PER_PAGE)
    query = request.query_params.get("q", "").strip() or None
    language = request.query_params.get("language", "").strip() or None
    only_buyers = request.query_params.get("buyers", "") in {"1", "on", "true"}
    only_blocked = request.query_params.get("blocked", "") in {"1", "on", "true"}

    rows, total = await users_service.list_users(
        db,
        query=query,
        language=language,
        only_buyers=only_buyers,
        only_blocked=only_blocked,
        limit=per_page,
        offset=offset,
    )
    return await render(
        request,
        "users/list.html",
        {
            "rows": rows,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": max(1, (total + per_page - 1) // per_page),
            "query": query or "",
            "language": language or "",
            "only_buyers": only_buyers,
            "only_blocked": only_blocked,
            "counters": await users_service.counters(db),
            "languages": settings.locales,
        },
        db=db,
    )


@router.get("/users/{user_id}", response_class=HTMLResponse)
async def user_detail(
    user_id: int, request: Request, db: deps.Db, admin=UsersAdmin
) -> HTMLResponse:
    user = await users_service.get(db, user_id)
    if user is None:
        flash(request, "Пользователь не найден", "err")
        return deps.redirect(deps.admin_url("users"))
    return await render(
        request,
        "users/detail.html",
        {
            "user": user,
            "stats": await users_service.stats(db, user.id),
            "orders": await users_service.user_orders(db, user.id, limit=20),
        },
        db=db,
    )


@router.post("/users/{user_id}/block")
async def user_block(user_id: int, request: Request, db: deps.Db, admin=UsersAdmin):
    await deps.verify_csrf(request)
    form = await request.form()
    blocked = deps.form_bool(form, "blocked")
    user = await users_service.set_blocked(db, user_id, blocked)
    if user is None:
        flash(request, "Пользователь не найден", "err")
        return deps.redirect(deps.admin_url("users"))

    await audit.record(
        db,
        admin_id=admin.id,
        admin_login=admin.login,
        action="block" if blocked else "unblock",
        entity="user",
        entity_id=user.id,
        summary=f"Блокировка: {blocked}",
        ip=deps.client_ip(request),
        user_agent=deps.user_agent(request),
    )
    flash(request, "Пользователь заблокирован" if blocked else "Блокировка снята")
    return deps.redirect(deps.admin_url("users", str(user_id)))


@router.post("/users/{user_id}/notes")
async def user_notes(user_id: int, request: Request, db: deps.Db, admin=UsersAdmin):
    await deps.verify_csrf(request)
    form = await request.form()
    notes = deps.form_str(form, "notes") or None
    user = await users_service.set_notes(db, user_id, notes)
    if user is None:
        flash(request, "Пользователь не найден", "err")
        return deps.redirect(deps.admin_url("users"))
    flash(request, "Заметка сохранена")
    return deps.redirect(deps.admin_url("users", str(user_id)))


@router.post("/users/{user_id}/message")
async def user_message(user_id: int, request: Request, db: deps.Db, admin=UsersAdmin):
    """Личное сообщение пользователю из карточки."""
    await deps.verify_csrf(request)
    form = await request.form()
    text = deps.form_str(form, "text")
    user = await users_service.get(db, user_id)
    if user is None or not text:
        flash(request, "Нужен текст сообщения", "err")
        return deps.redirect(deps.admin_url("users", str(user_id)))

    from app.bot import sender

    result = await sender.send_broadcast_message(tg_id=user.tg_id, text=text)
    if result == "ok":
        flash(request, "Сообщение отправлено")
    elif result == "blocked":
        flash(request, "Пользователь заблокировал бота", "err")
        await users_service.mark_bot_blocked(db, user.tg_id, True)
    else:
        flash(request, "Не удалось отправить", "err")

    await audit.record(
        db,
        admin_id=admin.id,
        admin_login=admin.login,
        action="message",
        entity="user",
        entity_id=user.id,
        summary=text[:200],
        ip=deps.client_ip(request),
        user_agent=deps.user_agent(request),
    )
    return deps.redirect(deps.admin_url("users", str(user_id)))
