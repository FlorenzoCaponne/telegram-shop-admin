"""Редактор контента бота без кода (ТЗ п.30–п.33).

Тексты, эмодзи, картинки и кнопки каждого экрана меняются из браузера;
после сохранения сбрасывается кэш, и бот отвечает уже по-новому.
"""
from __future__ import annotations

from typing import Any, Sequence

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.admin import deps
from app.admin.templating import flash, render
from app.core.config import settings
from app.core.security import Perm
from app.models import (
    ButtonAction,
    ButtonSetting,
    EmojiSetting,
    ImageSetting,
    TextSetting,
)
from app.services import audit, cms
from app.services.defaults import BUTTONS as DEFAULT_BUTTONS

router = APIRouter(tags=["admin-content"])

CmsAdmin = Depends(deps.require_perm(Perm.cms))
SCREENS: tuple[str, ...] = tuple(DEFAULT_BUTTONS.keys())
BUTTON_STYLES = ["primary", "secondary", "danger", "default"]


async def _log(
    db, request: Request, admin, *, action: str, entity: str, entity_id: int, summary: str
) -> None:
    await audit.record(
        db,
        admin_id=admin.id,
        admin_login=admin.login,
        action=action,
        entity=entity,
        entity_id=entity_id,
        summary=summary,
        ip=deps.client_ip(request),
        user_agent=deps.user_agent(request),
    )


def _i18n_from_form(form: Any, prefix: str, current: Any = None) -> dict[str, str]:
    value: dict[str, str] = dict(current or {}) if isinstance(current, dict) else {}
    for locale in settings.locales:
        raw = form.get(f"{prefix}__{locale}")
        if raw is not None:
            value[locale] = str(raw)
    return value


# =====================================================================
#  ТЕКСТЫ
# =====================================================================
@router.get("/texts", response_class=HTMLResponse)
async def texts_page(request: Request, db: deps.Db, admin=CmsAdmin) -> HTMLResponse:
    section = request.query_params.get("section", "").strip() or None
    stmt = select(TextSetting).order_by(TextSetting.section, TextSetting.key)
    if section:
        stmt = stmt.where(TextSetting.section == section)
    rows: Sequence[TextSetting] = (await db.execute(stmt)).scalars().all()

    sections = sorted(
        {
            str(value)
            for value in (
                await db.execute(select(TextSetting.section).distinct())
            ).scalars()
            if value
        }
    )
    return await render(
        request,
        "content/texts.html",
        {"rows": rows, "sections": sections, "section": section or ""},
        db=db,
    )


@router.post("/texts/save")
async def texts_save(request: Request, db: deps.Db, admin=CmsAdmin):
    """Сохраняет все тексты текущей секции одним действием."""
    await deps.verify_csrf(request)
    form = await request.form()
    section = deps.form_str(form, "section")

    stmt = select(TextSetting)
    if section:
        stmt = stmt.where(TextSetting.section == section)
    rows = (await db.execute(stmt)).scalars().all()

    changed = 0
    for row in rows:
        new_value = _i18n_from_form(form, f"text__{row.key}", row.value)
        is_html = deps.form_bool(form, f"html__{row.key}")
        if new_value != (row.value or {}) or bool(row.is_html) != is_html:
            row.value = new_value
            row.is_html = is_html
            changed += 1
    await db.commit()
    cms.invalidate_all()

    await _log(
        db,
        request,
        admin,
        action="save",
        entity="text",
        entity_id=0,
        summary=f"Тексты: изменено {changed}, секция {section or 'все'}",
    )
    flash(request, f"Сохранено текстов: {changed}")
    target = deps.admin_url("texts")
    return deps.redirect(f"{target}?section={section}" if section else target)


# =====================================================================
#  ЭМОДЗИ
# =====================================================================
@router.get("/emoji", response_class=HTMLResponse)
async def emoji_page(request: Request, db: deps.Db, admin=CmsAdmin) -> HTMLResponse:
    rows = (
        (await db.execute(select(EmojiSetting).order_by(EmojiSetting.key)))
        .scalars()
        .all()
    )
    return await render(request, "content/emoji.html", {"rows": rows}, db=db)


@router.post("/emoji/save")
async def emoji_save(request: Request, db: deps.Db, admin=CmsAdmin):
    await deps.verify_csrf(request)
    form = await request.form()
    rows = (await db.execute(select(EmojiSetting))).scalars().all()

    changed = 0
    for row in rows:
        raw = form.get(f"emoji__{row.key}")
        if raw is None:
            continue
        value = str(raw).strip()[:16]
        if value != (row.value or ""):
            row.value = value
            changed += 1
    await db.commit()
    cms.invalidate_all()

    await _log(
        db,
        request,
        admin,
        action="save",
        entity="emoji",
        entity_id=0,
        summary=f"Эмодзи: изменено {changed}",
    )
    flash(request, f"Сохранено: {changed}")
    return deps.redirect(deps.admin_url("emoji"))


# =====================================================================
#  КАРТИНКИ
# =====================================================================
@router.get("/images", response_class=HTMLResponse)
async def images_page(request: Request, db: deps.Db, admin=CmsAdmin) -> HTMLResponse:
    rows = (
        (await db.execute(select(ImageSetting).order_by(ImageSetting.key)))
        .scalars()
        .all()
    )
    return await render(request, "content/images.html", {"rows": rows}, db=db)


@router.post("/images/save")
async def images_save(request: Request, db: deps.Db, admin=CmsAdmin):
    await deps.verify_csrf(request)
    form = await request.form()
    rows = (await db.execute(select(ImageSetting))).scalars().all()

    changed = 0
    for row in rows:
        raw = form.get(f"image__{row.key}")
        active = deps.form_bool(form, f"active__{row.key}")
        new_url = str(raw).strip() if raw is not None else (row.url or "")
        if new_url != (row.url or "") or bool(row.is_active) != active:
            if new_url != (row.url or ""):
                # Ссылка сменилась — кэш file_id Telegram больше не валиден.
                row.tg_file_id = None
            row.url = new_url or None
            row.is_active = active
            changed += 1
    await db.commit()
    cms.invalidate_all()

    await _log(
        db,
        request,
        admin,
        action="save",
        entity="image",
        entity_id=0,
        summary=f"Картинки: изменено {changed}",
    )
    flash(request, f"Сохранено: {changed}")
    return deps.redirect(deps.admin_url("images"))


# =====================================================================
#  КНОПКИ
# =====================================================================
@router.get("/buttons", response_class=HTMLResponse)
async def buttons_page(request: Request, db: deps.Db, admin=CmsAdmin) -> HTMLResponse:
    screen = request.query_params.get("screen", "").strip() or SCREENS[0]
    rows = (
        (
            await db.execute(
                select(ButtonSetting)
                .where(ButtonSetting.screen == screen)
                .order_by(ButtonSetting.row, ButtonSetting.position, ButtonSetting.id)
            )
        )
        .scalars()
        .all()
    )
    return await render(
        request,
        "content/buttons.html",
        {
            "rows": rows,
            "screen": screen,
            "screens": SCREENS,
            "actions": [item.value for item in ButtonAction],
            "styles": BUTTON_STYLES,
            "button": None,
        },
        db=db,
    )


@router.get("/buttons/new", response_class=HTMLResponse)
async def button_new(request: Request, db: deps.Db, admin=CmsAdmin) -> HTMLResponse:
    screen = request.query_params.get("screen", "").strip() or SCREENS[0]
    return await render(
        request,
        "content/_button_form.html",
        {
            "button": None,
            "screen": screen,
            "screens": SCREENS,
            "actions": [item.value for item in ButtonAction],
            "styles": BUTTON_STYLES,
        },
        db=db,
    )


@router.get("/buttons/{button_id}", response_class=HTMLResponse)
async def button_edit(
    button_id: int, request: Request, db: deps.Db, admin=CmsAdmin
) -> HTMLResponse:
    button = await db.get(ButtonSetting, button_id)
    if button is None:
        flash(request, "Кнопка не найдена", "err")
        return deps.redirect(deps.admin_url("buttons"))
    return await render(
        request,
        "content/_button_form.html",
        {
            "button": button,
            "screen": button.screen,
            "screens": SCREENS,
            "actions": [item.value for item in ButtonAction],
            "styles": BUTTON_STYLES,
        },
        db=db,
    )


@router.post("/buttons/save")
async def button_save(request: Request, db: deps.Db, admin=CmsAdmin):
    await deps.verify_csrf(request)
    form = await request.form()
    button_id = deps.form_int(form, "id")
    screen = deps.form_str(form, "screen", SCREENS[0])
    code = deps.form_str(form, "code")
    if not code:
        flash(request, "Код кнопки обязателен", "err")
        return deps.redirect(f"{deps.admin_url('buttons')}?screen={screen}")

    button = await db.get(ButtonSetting, button_id) if button_id else None
    if button is None:
        button = ButtonSetting(screen=screen, code=code, action=ButtonAction.NOOP)
        db.add(button)

    button.screen = screen
    button.code = code
    button.title = _i18n_from_form(form, "title", button.title)
    button.emoji = deps.form_str(form, "emoji") or None
    button.action = ButtonAction(deps.form_str(form, "action", "noop"))
    button.url = deps.form_str(form, "url") or None
    payload_raw = deps.form_str(form, "payload")
    button.payload = payload_raw or None
    button.row = deps.form_int(form, "row", 0) or 0
    button.position = deps.form_int(form, "position", 0) or 0
    button.style = deps.form_str(form, "style", "default")
    button.is_wide = deps.form_bool(form, "is_wide")
    button.is_active = deps.form_bool(form, "is_active")

    await db.commit()
    cms.invalidate_all()

    await _log(
        db,
        request,
        admin,
        action="save",
        entity="button",
        entity_id=button.id,
        summary=f"Кнопка {screen}/{code}",
    )
    flash(request, "Кнопка сохранена")
    return deps.redirect(f"{deps.admin_url('buttons')}?screen={screen}")


@router.post("/buttons/{button_id}/delete")
async def button_delete(button_id: int, request: Request, db: deps.Db, admin=CmsAdmin):
    await deps.verify_csrf(request)
    button = await db.get(ButtonSetting, button_id)
    screen = button.screen if button else SCREENS[0]
    if button is not None:
        await db.delete(button)
        await db.commit()
        cms.invalidate_all()
        await _log(
            db,
            request,
            admin,
            action="delete",
            entity="button",
            entity_id=button_id,
            summary=f"Удалена кнопка {screen}",
        )
        flash(request, "Кнопка удалена")
    return deps.redirect(f"{deps.admin_url('buttons')}?screen={screen}")


@router.post("/buttons/{button_id}/toggle")
async def button_toggle(button_id: int, request: Request, db: deps.Db, admin=CmsAdmin):
    await deps.verify_csrf(request)
    button = await db.get(ButtonSetting, button_id)
    screen = button.screen if button else SCREENS[0]
    if button is not None:
        button.is_active = not button.is_active
        await db.commit()
        cms.invalidate_all()
    return deps.redirect(f"{deps.admin_url('buttons')}?screen={screen}")


@router.post("/buttons/{button_id}/move")
async def button_move(button_id: int, request: Request, db: deps.Db, admin=CmsAdmin):
    """Сдвиг кнопки по рядам/позициям без кода."""
    await deps.verify_csrf(request)
    form = await request.form()
    direction = deps.form_str(form, "direction", "up")
    button = await db.get(ButtonSetting, button_id)
    if button is None:
        return deps.redirect(deps.admin_url("buttons"))

    if direction == "up":
        button.position = max(0, int(button.position or 0) - 1)
    elif direction == "down":
        button.position = int(button.position or 0) + 1
    elif direction == "row_up":
        button.row = max(0, int(button.row or 0) - 1)
    elif direction == "row_down":
        button.row = int(button.row or 0) + 1

    await db.commit()
    cms.invalidate_all()
    return deps.redirect(f"{deps.admin_url('buttons')}?screen={button.screen}")
