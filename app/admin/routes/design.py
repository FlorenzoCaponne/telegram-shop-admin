"""Внешний вид бота: глобальные настройки и блоки экранов (ТЗ п.31–п.34).

Любой элемент экрана (картинка, заголовок, текст, инфоблок, CTA, разделитель)
добавляется и переставляется без правки кода.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.admin import deps
from app.admin.templating import flash, render
from app.core.config import settings as app_settings
from app.core.security import Perm
from app.models import BlockType, DesignBlock
from app.services import audit, cms
from app.services.defaults import BUTTONS as DEFAULT_BUTTONS
from app.services.defaults import DESIGN, DESIGN_LABELS

router = APIRouter(tags=["admin-design"])

CmsAdmin = Depends(deps.require_perm(Perm.cms))
SCREENS: tuple[str, ...] = tuple(DEFAULT_BUTTONS.keys())


def _coerce(key: str, raw: str | None, present: bool) -> Any:
    """Привести значение из формы к типу дефолта."""
    default = DESIGN.get(key)
    if isinstance(default, bool):
        return present
    if isinstance(default, int):
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            return default
    return "" if raw is None else str(raw)


async def _log(db, request: Request, admin, *, entity_id: int, summary: str, action: str = "save") -> None:
    await audit.record(
        db,
        admin_id=admin.id,
        admin_login=admin.login,
        action=action,
        entity="design",
        entity_id=entity_id,
        summary=summary,
        ip=deps.client_ip(request),
        user_agent=deps.user_agent(request),
    )


@router.get("/design", response_class=HTMLResponse)
async def design_index(request: Request, db: deps.Db, admin=CmsAdmin) -> HTMLResponse:
    current = await cms.get_design_settings(db)
    items = [
        {
            "key": key,
            "label": DESIGN_LABELS.get(key, key),
            "value": current.get(key, default),
            "default": default,
            "kind": (
                "bool"
                if isinstance(default, bool)
                else "int"
                if isinstance(default, int)
                else "text"
            ),
        }
        for key, default in DESIGN.items()
    ]
    return await render(request, "design/index.html", {"items": items}, db=db)


@router.post("/design/save")
async def design_save(request: Request, db: deps.Db, admin=CmsAdmin):
    await deps.verify_csrf(request)
    form = await request.form()
    changed = 0
    current = await cms.get_design_settings(db)

    for key in DESIGN:
        field = f"design__{key}"
        present = field in form
        if not present and not isinstance(DESIGN.get(key), bool):
            continue
        raw = form.get(field)
        value = _coerce(key, None if raw is None else str(raw), present)
        if value != current.get(key):
            await cms.set_design_value(db, key, value)
            changed += 1

    await _log(db, request, admin, entity_id=0, summary=f"Дизайн: изменено {changed}")
    flash(request, f"Сохранено настроек: {changed}")
    return deps.redirect(deps.admin_url("design"))


@router.get("/design/blocks", response_class=HTMLResponse)
async def blocks_page(request: Request, db: deps.Db, admin=CmsAdmin) -> HTMLResponse:
    screen = request.query_params.get("screen", "").strip() or SCREENS[0]
    rows = (
        (
            await db.execute(
                select(DesignBlock)
                .where(DesignBlock.screen == screen)
                .order_by(DesignBlock.position, DesignBlock.id)
            )
        )
        .scalars()
        .all()
    )
    return await render(
        request,
        "design/blocks.html",
        {
            "rows": rows,
            "screen": screen,
            "screens": SCREENS,
            "block_types": [item.value for item in BlockType],
            "locales": app_settings.locales,
        },
        db=db,
    )


@router.post("/design/blocks/save")
async def block_save(request: Request, db: deps.Db, admin=CmsAdmin):
    await deps.verify_csrf(request)
    form = await request.form()
    block_id = deps.form_int(form, "id")
    screen = deps.form_str(form, "screen", SCREENS[0])

    block = await db.get(DesignBlock, block_id) if block_id else None
    if block is None:
        block = DesignBlock(screen=screen, block_type=BlockType.TEXT, position=100)
        db.add(block)

    title: dict[str, str] = dict(block.title or {})
    content: dict[str, str] = dict(block.content or {})
    for locale in app_settings.locales:
        raw_title = form.get(f"title__{locale}")
        if raw_title is not None:
            title[locale] = str(raw_title)
        raw_content = form.get(f"content__{locale}")
        if raw_content is not None:
            content[locale] = str(raw_content)

    block.screen = screen
    block.block_type = BlockType(deps.form_str(form, "block_type", "text"))
    block.title = title
    block.content = content
    block.emoji = deps.form_str(form, "emoji") or None
    block.image_url = deps.form_str(form, "image_url") or None
    block.position = deps.form_int(form, "position", 100) or 100
    block.is_active = deps.form_bool(form, "is_active")
    separator = deps.form_str(form, "separator")
    config = dict(block.config or {})
    if separator:
        config["separator"] = separator
    block.config = config

    await db.commit()
    await cms.invalidate_all()
    await _log(
        db, request, admin, entity_id=block.id, summary=f"Блок {screen}/{block.block_type}"
    )
    flash(request, "Блок сохранён")
    return deps.redirect(f"{deps.admin_url('design', 'blocks')}?screen={screen}")


@router.post("/design/blocks/{block_id}/delete")
async def block_delete(block_id: int, request: Request, db: deps.Db, admin=CmsAdmin):
    await deps.verify_csrf(request)
    block = await db.get(DesignBlock, block_id)
    screen = block.screen if block else SCREENS[0]
    if block is not None:
        await db.delete(block)
        await db.commit()
        await cms.invalidate_all()
        await _log(
            db,
            request,
            admin,
            entity_id=block_id,
            summary=f"Удалён блок экрана {screen}",
            action="delete",
        )
        flash(request, "Блок удалён")
    return deps.redirect(f"{deps.admin_url('design', 'blocks')}?screen={screen}")


@router.post("/design/blocks/{block_id}/toggle")
async def block_toggle(block_id: int, request: Request, db: deps.Db, admin=CmsAdmin):
    await deps.verify_csrf(request)
    block = await db.get(DesignBlock, block_id)
    screen = block.screen if block else SCREENS[0]
    if block is not None:
        block.is_active = not block.is_active
        await db.commit()
        await cms.invalidate_all()
    return deps.redirect(f"{deps.admin_url('design', 'blocks')}?screen={screen}")


@router.post("/design/blocks/{block_id}/move")
async def block_move(block_id: int, request: Request, db: deps.Db, admin=CmsAdmin):
    await deps.verify_csrf(request)
    form = await request.form()
    direction = deps.form_str(form, "direction", "up")
    block = await db.get(DesignBlock, block_id)
    if block is None:
        return deps.redirect(deps.admin_url("design", "blocks"))

    step = -10 if direction == "up" else 10
    block.position = max(0, int(block.position or 100) + step)
    await db.commit()
    await cms.invalidate_all()
    return deps.redirect(f"{deps.admin_url('design', 'blocks')}?screen={block.screen}")
