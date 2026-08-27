"""Системные настройки: магазин, Telegram, платежи, бот (ТЗ п.35, п.55).

Секреты (API-ключи) никогда не отдаются в браузер: видно только факт
заполненности; пустое поле при сохранении означает «не менять».
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.admin import deps
from app.admin.templating import flash, render
from app.core.security import Perm
from app.payments import base as pay_base
from app.payments import reset_provider
from app.services import audit, cms, payments as payments_service
from app.services.defaults import GROUPS

router = APIRouter(tags=["admin-settings"])

SettingsAdmin = Depends(deps.require_perm(Perm.settings))
BOOL_SUFFIXES = (
    "require_subscription",
    "enabled",
    "test_mode",
    "send_metadata",
    "webhook_require_signature",
    "notify_admins_on_order",
)
INT_SUFFIXES = ("ttl_seconds", "rate_limit_per_second", "broadcast_rate", "channel_id")


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request, db: deps.Db, admin=SettingsAdmin
) -> HTMLResponse:
    group = request.query_params.get("group", "").strip() or "shop"
    if group not in GROUPS:
        group = "shop"

    items = await cms.settings_meta(db, group)
    selected_methods = [
        int(code)
        for code in (await cms.setting(db, "payment.methods", []) or [])
        if str(code).isdigit()
    ]
    return await render(
        request,
        "settings/index.html",
        {
            "items": [item for item in items if item["key"] != "payment.methods"],
            "group": group,
            "groups": GROUPS,
            "methods": pay_base.METHODS,
            "selected_methods": selected_methods,
            "provider": await payments_service.provider_status(db),
            "bool_suffixes": BOOL_SUFFIXES,
            "int_suffixes": INT_SUFFIXES,
        },
        db=db,
    )


@router.post("/settings/save")
async def settings_save(request: Request, db: deps.Db, admin=SettingsAdmin):
    await deps.verify_csrf(request)
    form = await request.form()
    group = deps.form_str(form, "group", "shop")
    if group not in GROUPS:
        group = "shop"

    items = await cms.settings_meta(db, group)
    saved = 0
    for item in items:
        key = item["key"]
        if key == "payment.methods":
            continue
        field = f"set__{key}"
        present = field in form
        raw = form.get(field)

        if key.endswith(BOOL_SUFFIXES):
            value: object = present and str(raw) not in {"0", "false", "off", ""}
        elif key.endswith(INT_SUFFIXES):
            if not present:
                continue
            text = str(raw or "").strip()
            if not text:
                value = item["default"]
            else:
                try:
                    value = int(text)
                except ValueError:
                    flash(request, f"Неверное число в поле {item['label']}", "err")
                    continue
        else:
            if not present:
                continue
            value = str(raw or "")
            if item["is_secret"] and not value:
                # Пустое поле секрета = оставить текущее значение.
                continue

        await cms.set_setting(db, key, value)
        saved += 1

    if group == "payment" and deps.form_bool(form, "methods_present"):
        methods = [
            int(code) for code in form.getlist("methods") if str(code).isdigit()
        ] or list(pay_base.DEFAULT_METHODS)
        await cms.set_setting(db, "payment.methods", methods)
        saved += 1

    # Клиент платёжки пересобирается с новыми креденшелами.
    if group == "payment":
        await reset_provider()

    await audit.record(
        db,
        admin_id=admin.id,
        admin_login=admin.login,
        action="save",
        entity="settings",
        entity_id=0,
        summary=f"Группа {group}: сохранено {saved}",
        ip=deps.client_ip(request),
        user_agent=deps.user_agent(request),
    )
    flash(request, f"Настроек сохранено: {saved}")
    return deps.redirect(f"{deps.admin_url('settings')}?group={group}")


@router.post("/settings/test-payment")
async def settings_test_payment(request: Request, db: deps.Db, admin=SettingsAdmin):
    """Проверка текущей конфигурации платёжки без создания платежа."""
    await deps.verify_csrf(request)
    status = await payments_service.provider_status(db)
    if status.configured:
        flash(
            request,
            f"Провайдер {status.provider}: ключи на месте, методы {status.methods}",
        )
    else:
        flash(request, f"Провайдер не настроен: {status.message}", "err")
    return deps.redirect(f"{deps.admin_url('settings')}?group=payment")
