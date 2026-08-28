"""Администраторы панели и роли (ТЗ п.38).

Пароли хранятся только в виде argon2-хеша; при создании пароль можно
сгенерировать автоматически — он показывается ровно один раз.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select

from app.admin import deps
from app.admin.templating import flash, render
from app.core.security import (
    AdminRole,
    Perm,
    ROLE_PERMISSIONS,
    generate_password,
    hash_password,
)
from app.models import Admin
from app.services import audit

router = APIRouter(tags=["admin-admins"])

AdminsAdmin = Depends(deps.require_perm(Perm.admins))


@router.get("/admins", response_class=HTMLResponse)
async def admins_list(request: Request, db: deps.Db, admin=AdminsAdmin) -> HTMLResponse:
    rows = (
        (await db.execute(select(Admin).order_by(Admin.id))).scalars().all()
    )
    return await render(
        request,
        "admins/list.html",
        {
            "rows": rows,
            "roles": [item.value for item in AdminRole],
            "permissions": {
                role.value: sorted(perm.value for perm in perms)
                for role, perms in ROLE_PERMISSIONS.items()
            },
        },
        db=db,
    )


@router.post("/admins/save")
async def admin_save(request: Request, db: deps.Db, admin=AdminsAdmin):
    await deps.verify_csrf(request)
    form = await request.form()
    admin_id = deps.form_int(form, "id")
    login = deps.form_str(form, "login").strip()
    if not login:
        flash(request, "Логин обязателен", "err")
        return deps.redirect(deps.admin_url("admins"))

    duplicate = (
        await db.execute(
            select(Admin).where(func.lower(Admin.login) == login.lower())
        )
    ).scalar_one_or_none()
    if duplicate is not None and duplicate.id != admin_id:
        flash(request, "Такой логин уже занят", "err")
        return deps.redirect(deps.admin_url("admins"))

    item = await db.get(Admin, admin_id) if admin_id else None
    is_new = item is None
    password = deps.form_str(form, "password")
    generated = ""

    if item is None:
        if not password:
            generated = generate_password()
            password = generated
        item = Admin(login=login, password_hash=hash_password(password))
        db.add(item)
    elif password:
        item.password_hash = hash_password(password)
        item.failed_attempts = 0
        item.locked_until = None

    item.login = login
    item.email = deps.form_str(form, "email") or None
    try:
        item.role = AdminRole(deps.form_str(form, "role", AdminRole.MANAGER.value))
    except ValueError:
        item.role = AdminRole.MANAGER
    item.is_active = deps.form_bool(form, "is_active") if not is_new else True

    await db.commit()
    await audit.record(
        db,
        admin_id=admin.id,
        admin_login=admin.login,
        action="create" if is_new else "save",
        entity="admin",
        entity_id=item.id,
        summary=f"Админ {item.login}, роль {item.role}",
        ip=deps.client_ip(request),
        user_agent=deps.user_agent(request),
    )
    if generated:
        flash(request, f"Админ {item.login} создан. Пароль: {generated}")
    else:
        flash(request, "Сохранено")
    return deps.redirect(deps.admin_url("admins"))


@router.post("/admins/{admin_id}/toggle")
async def admin_toggle(admin_id: int, request: Request, db: deps.Db, admin=AdminsAdmin):
    await deps.verify_csrf(request)
    if admin_id == admin.id:
        flash(request, "Нельзя отключить самого себя", "err")
        return deps.redirect(deps.admin_url("admins"))

    item = await db.get(Admin, admin_id)
    if item is None:
        flash(request, "Админ не найден", "err")
        return deps.redirect(deps.admin_url("admins"))

    item.is_active = not item.is_active
    item.failed_attempts = 0
    item.locked_until = None
    await db.commit()
    await audit.record(
        db,
        admin_id=admin.id,
        admin_login=admin.login,
        action="toggle",
        entity="admin",
        entity_id=item.id,
        summary=f"Активен = {item.is_active}",
        ip=deps.client_ip(request),
        user_agent=deps.user_agent(request),
    )
    flash(request, "Статус обновлён")
    return deps.redirect(deps.admin_url("admins"))


@router.post("/admins/{admin_id}/unlock")
async def admin_unlock(admin_id: int, request: Request, db: deps.Db, admin=AdminsAdmin):
    """Снять блокировку после неудачных попыток входа."""
    await deps.verify_csrf(request)
    item = await db.get(Admin, admin_id)
    if item is not None:
        item.failed_attempts = 0
        item.locked_until = None
        await db.commit()
        flash(request, "Блокировка снята")
    return deps.redirect(deps.admin_url("admins"))


@router.post("/admins/{admin_id}/delete")
async def admin_delete(admin_id: int, request: Request, db: deps.Db, admin=AdminsAdmin):
    await deps.verify_csrf(request)
    if admin_id == admin.id:
        flash(request, "Нельзя удалить самого себя", "err")
        return deps.redirect(deps.admin_url("admins"))

    item = await db.get(Admin, admin_id)
    if item is None:
        flash(request, "Админ не найден", "err")
        return deps.redirect(deps.admin_url("admins"))

    remaining = int(
        (
            await db.execute(
                select(func.count(Admin.id)).where(
                    Admin.is_active.is_(True), Admin.id != admin_id
                )
            )
        ).scalar()
        or 0
    )
    if remaining == 0:
        flash(request, "Должен остаться хотя бы один активный админ", "err")
        return deps.redirect(deps.admin_url("admins"))

    login = item.login
    await db.delete(item)
    await db.commit()
    await audit.record(
        db,
        admin_id=admin.id,
        admin_login=admin.login,
        action="delete",
        entity="admin",
        entity_id=admin_id,
        summary=f"Удалён админ {login}",
        ip=deps.client_ip(request),
        user_agent=deps.user_agent(request),
    )
    flash(request, "Админ удалён")
    return deps.redirect(deps.admin_url("admins"))
