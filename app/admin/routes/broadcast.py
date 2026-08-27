"""Рассылки в админке (ТЗ п.39).

Кнопки рассылки задаются строками вида «Текст | https://...».
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.admin import deps
from app.admin.templating import flash, render
from app.core.config import settings
from app.core.security import Perm
from app.services import audit, broadcast as broadcast_service

router = APIRouter(tags=["admin-broadcast"])

BroadcastAdmin = Depends(deps.require_perm(Perm.broadcast))
PER_PAGE = 50


def _parse_buttons(raw: str) -> list[dict[str, Any]]:
    """Каждая строка: «Надпись | URL»."""
    buttons: list[dict[str, Any]] = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        text, url = line.split("|", 1)
        text, url = text.strip(), url.strip()
        if text and url:
            buttons.append({"text": text, "url": url})
    return buttons


def _buttons_to_text(buttons: Any) -> str:
    lines = []
    for item in buttons or []:
        text = str(item.get("text") or item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        if text and url:
            lines.append(f"{text} | {url}")
    return "\n".join(lines)


@router.get("/broadcasts", response_class=HTMLResponse)
async def broadcasts_list(
    request: Request, db: deps.Db, admin=BroadcastAdmin
) -> HTMLResponse:
    page, per_page, offset = deps.page_params(request, PER_PAGE)
    rows, total = await broadcast_service.list_broadcasts(
        db, limit=per_page, offset=offset
    )
    return await render(
        request,
        "broadcast/list.html",
        {
            "rows": rows,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": max(1, (total + per_page - 1) // per_page),
        },
        db=db,
    )


@router.get("/broadcasts/new", response_class=HTMLResponse)
async def broadcast_new(
    request: Request, db: deps.Db, admin=BroadcastAdmin
) -> HTMLResponse:
    return await render(
        request,
        "broadcast/form.html",
        {
            "item": None,
            "buttons_text": "",
            "segments": broadcast_service.SEGMENTS,
            "languages": settings.locales,
            "audience_count": await broadcast_service.audience_size(db, {"segment": "all"}),
        },
        db=db,
    )


@router.get("/broadcasts/{broadcast_id}", response_class=HTMLResponse)
async def broadcast_edit(
    broadcast_id: int, request: Request, db: deps.Db, admin=BroadcastAdmin
) -> HTMLResponse:
    item = await broadcast_service.get_broadcast(db, broadcast_id)
    if item is None:
        flash(request, "Рассылка не найдена", "err")
        return deps.redirect(deps.admin_url("broadcasts"))
    return await render(
        request,
        "broadcast/form.html",
        {
            "item": item,
            "buttons_text": _buttons_to_text(item.buttons),
            "segments": broadcast_service.SEGMENTS,
            "languages": settings.locales,
            "audience_count": await broadcast_service.audience_size(
                db, item.audience or {}
            ),
        },
        db=db,
    )


@router.post("/broadcasts/save")
async def broadcast_save(request: Request, db: deps.Db, admin=BroadcastAdmin):
    await deps.verify_csrf(request)
    form = await request.form()
    broadcast_id = deps.form_int(form, "id")

    text = deps.form_str(form, "text")
    if not text:
        flash(request, "Текст рассылки обязателен", "err")
        return deps.redirect(deps.admin_url("broadcasts"))

    data = {
        "name": deps.form_str(form, "name", "Без названия"),
        "text": text,
        "image_url": deps.form_str(form, "image_url"),
        "buttons": _parse_buttons(deps.form_str(form, "buttons")),
        "segment": deps.form_str(form, "segment", "all"),
        "language": deps.form_str(form, "language"),
    }
    item = await broadcast_service.save_broadcast(
        db, broadcast_id=broadcast_id, data=data, admin_id=admin.id
    )
    await audit.record(
        db,
        admin_id=admin.id,
        admin_login=admin.login,
        action="save",
        entity="broadcast",
        entity_id=item.id,
        summary=f"Рассылка {item.name}",
        ip=deps.client_ip(request),
        user_agent=deps.user_agent(request),
    )
    flash(request, "Рассылка сохранена")
    return deps.redirect(deps.admin_url("broadcasts", str(item.id)))


@router.post("/broadcasts/{broadcast_id}/start")
async def broadcast_start(
    broadcast_id: int, request: Request, db: deps.Db, admin=BroadcastAdmin
):
    await deps.verify_csrf(request)
    item = await broadcast_service.start(db, broadcast_id)
    if item is None:
        flash(request, "Рассылка не найдена", "err")
        return deps.redirect(deps.admin_url("broadcasts"))
    await audit.record(
        db,
        admin_id=admin.id,
        admin_login=admin.login,
        action="start",
        entity="broadcast",
        entity_id=item.id,
        summary=f"Запуск рассылки, аудитория {item.total}",
        ip=deps.client_ip(request),
        user_agent=deps.user_agent(request),
    )
    flash(request, "Рассылка запущена")
    return deps.redirect(deps.admin_url("broadcasts", str(broadcast_id)))


@router.post("/broadcasts/{broadcast_id}/pause")
async def broadcast_pause(
    broadcast_id: int, request: Request, db: deps.Db, admin=BroadcastAdmin
):
    await deps.verify_csrf(request)
    await broadcast_service.pause(db, broadcast_id)
    flash(request, "Рассылка приостановлена")
    return deps.redirect(deps.admin_url("broadcasts", str(broadcast_id)))


@router.post("/broadcasts/{broadcast_id}/cancel")
async def broadcast_cancel(
    broadcast_id: int, request: Request, db: deps.Db, admin=BroadcastAdmin
):
    await deps.verify_csrf(request)
    await broadcast_service.cancel(db, broadcast_id)
    flash(request, "Рассылка отменена")
    return deps.redirect(deps.admin_url("broadcasts", str(broadcast_id)))


@router.post("/broadcasts/{broadcast_id}/delete")
async def broadcast_delete(
    broadcast_id: int, request: Request, db: deps.Db, admin=BroadcastAdmin
):
    await deps.verify_csrf(request)
    ok = await broadcast_service.delete_broadcast(db, broadcast_id)
    flash(
        request,
        "Удалено" if ok else "Сначала остановите рассылку",
        "ok" if ok else "err",
    )
    return deps.redirect(deps.admin_url("broadcasts"))


@router.post("/broadcasts/audience", response_class=HTMLResponse)
async def audience_preview(
    request: Request, db: deps.Db, admin=BroadcastAdmin
) -> HTMLResponse:
    """HTMX: пересчёт размера аудитории при смене сегмента."""
    form = await request.form()
    result = await broadcast_service.preview(
        db,
        {
            "segment": deps.form_str(form, "segment", "all"),
            "language": deps.form_str(form, "language"),
        },
    )
    return await render(
        request,
        "fragments/audience_count.html",
        {"audience_count": result["count"], "audience": result["audience"]},
        db=db,
    )
