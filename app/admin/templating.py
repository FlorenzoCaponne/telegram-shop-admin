"""Jinja2-окружение админки: фильтры, глобалы, flash-сообщения."""
from __future__ import annotations

from decimal import Decimal
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services import cms

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

FLASH_KEY = "flashes"
DATETIME_FORMAT = "%d.%m.%Y %H:%M"


def money(value: Any, currency: str = "") -> str:
    """Формат денег без лишних нулей: 1 990 ₽."""
    if value is None or value == "":
        return "—"
    try:
        amount = Decimal(str(value))
    except Exception:
        return str(value)
    quantized = amount.quantize(Decimal("0.01"))
    if quantized == quantized.to_integral_value():
        text = f"{int(quantized):,}".replace(",", "\u00a0")
    else:
        text = f"{quantized:,.2f}".replace(",", "\u00a0")
    symbol = currency or ""
    return f"{text} {symbol}".strip()


def dt(value: Any, fmt: str = DATETIME_FORMAT) -> str:
    if not value:
        return "—"
    if isinstance(value, datetime):
        return value.strftime(fmt)
    return str(value)


def i18n(value: Any, locale: str = "") -> str:
    """Значение многоязычного поля для отображения в таблицах."""
    return str(cms.pick_locale(value, locale or settings.default_locale) or "")


def yesno(value: Any, yes: str = "Да", no: str = "Нет") -> str:
    return yes if bool(value) else no


def status_class(value: Any) -> str:
    """CSS-класс бейджа по статусу."""
    status = str(getattr(value, "value", value) or "").lower()
    if status in {"paid", "completed", "confirmed", "done", "available", "delivered"}:
        return "badge badge-ok"
    if status in {"created", "payment_pending", "pending", "processing", "running", "reserved", "draft", "paused"}:
        return "badge badge-wait"
    if status in {"failed", "cancelled", "canceled", "error", "expired", "chargebacked"}:
        return "badge badge-err"
    return "badge"


templates.env.filters["money"] = money
templates.env.filters["dt"] = dt
templates.env.filters["i18n"] = i18n
templates.env.filters["yesno"] = yesno
templates.env.filters["status_class"] = status_class

templates.env.globals["admin_path"] = settings.admin_path
templates.env.globals["app_env"] = settings.app_env
templates.env.globals["locales"] = settings.locales
templates.env.globals["default_locale"] = settings.default_locale


def flash(request: Request, message: str, level: str = "ok") -> None:
    """Добавить одноразовое сообщение в сессию."""
    items = list(request.session.get(FLASH_KEY) or [])
    items.append({"message": message, "level": level})
    request.session[FLASH_KEY] = items[-10:]


def pop_flashes(request: Request) -> list[dict[str, str]]:
    items = list(request.session.get(FLASH_KEY) or [])
    if items:
        request.session[FLASH_KEY] = []
    return items


async def base_context(
    request: Request, db: AsyncSession | None = None, **extra: Any
) -> dict[str, Any]:
    """Общие переменные для всех шаблонов."""
    shop_name = settings.app_name
    admin_title = settings.app_name
    if db is not None:
        shop_name = str(await cms.setting(db, "shop.name", shop_name) or shop_name)
        admin_title = str(
            await cms.setting(db, "shop.admin_title", shop_name) or shop_name
        )

    context: dict[str, Any] = {
        "request": request,
        "shop_name": shop_name,
        "admin_title": admin_title,
        "admin_path": settings.admin_path.rstrip("/"),
        "app_env": settings.app_env,
        "locales": settings.locales,
        "default_locale": settings.default_locale,
        "current_path": request.url.path,
        "flashes": pop_flashes(request),
        "csrf_token": request.session.get("csrf", ""),
        "admin": getattr(request.state, "admin", None),
    }
    context.update(extra)
    return context


async def render(
    request: Request,
    template: str,
    context: dict[str, Any] | None = None,
    status_code: int = 200,
    db: AsyncSession | None = None,
) -> HTMLResponse:
    """Рендер шаблона с базовым контекстом."""
    ctx = await base_context(request, db, **(context or {}))
    return templates.TemplateResponse(
        request=request, name=template, context=ctx, status_code=status_code
    )
