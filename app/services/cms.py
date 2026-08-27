"""Доступ к контенту CMS: тексты, emoji, изображения, кнопки, дизаи́н, настрои́ки.

Всё чтение кэшируется в Redis с версионированием namespace: любое изменение
в админке делает cache.bump(NS) и бот моментально видит новые данные
(ТЗ п.30-п.36, п.55). Секреты хранятся шифрованными и расшифровываются только тут.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import (
    NS_BUTTONS,
    NS_DESIGN,
    NS_EMOJI,
    NS_IMAGES,
    NS_SETTINGS,
    NS_TEXTS,
    cache,
)
from app.core.security import decrypt_secret, encrypt_secret
from app.models import (
    ButtonSetting,
    DesignBlock,
    DesignSetting,
    EmojiSetting,
    ImageSetting,
    SystemSetting,
    TextSetting,
)
from app.services import defaults

log = structlog.get_logger(__name__)

LOCALES = defaults.LOCALES
DEFAULT_LOCALE = defaults.DEFAULT_LOCALE


# =====================================================================
#  ЛОКАЛИ
# =====================================================================
def normalize_locale(locale: str | None) -> str:
    """'ru-RU' → 'ru'; неизвестные языки → язык по умолчанию."""
    value = (locale or "").strip().lower().replace("_", "-")
    if not value:
        return DEFAULT_LOCALE
    short = value.split("-", 1)[0]
    return short if short in LOCALES else DEFAULT_LOCALE


def pick_locale(value: Any, locale: str | None = None) -> str:
    """Взять строку из мультиязычного JSON с фоллбэком на другие языки."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return str(value)
    wanted = normalize_locale(locale)
    for candidate in (wanted, DEFAULT_LOCALE, *LOCALES):
        text = value.get(candidate)
        if isinstance(text, str) and text.strip():
            return text
    for text in value.values():
        if isinstance(text, str) and text.strip():
            return text
    return ""


# =====================================================================
#  ТЕКСТЫ
# =====================================================================
async def get_texts(db: AsyncSession) -> dict[str, dict[str, Any]]:
    """{key: {"value": {...}, "is_html": bool, "section": str}}"""

    async def loader() -> dict[str, dict[str, Any]]:
        rows = (await db.execute(select(TextSetting))).scalars().all()
        return {
            row.key: {
                "value": row.value or {},
                "is_html": bool(row.is_html),
                "section": row.section,
                "label": row.label,
            }
            for row in rows
        }

    return await cache.get_or_set(NS_TEXTS, "all", loader, ttl=600)


async def t(db: AsyncSession, key: str, locale: str = "", **fmt: Any) -> str:
    """Получить текст по ключу с подстановкой переменных.

    Если ключа нет ни в БД, ни в defaults — возвращается "[key]",
    чтобы проблема была заметна, но бот не падал.
    """
    texts = await get_texts(db)
    entry = texts.get(key)
    raw = entry["value"] if entry else defaults.TEXTS.get(key, {}).get("value")
    text = pick_locale(raw, locale)
    if not text:
        return f"[{key}]"
    if fmt:
        try:
            return text.format(**fmt)
        except (KeyError, IndexError, ValueError):
            log.warning("cms.text_format_failed", key=key)
            return text
    return text


async def is_html(db: AsyncSession, key: str) -> bool:
    texts = await get_texts(db)
    entry = texts.get(key)
    if entry is not None:
        return bool(entry["is_html"])
    return bool(defaults.TEXTS.get(key, {}).get("is_html", False))


# =====================================================================
#  EMOJI
# =====================================================================
async def get_emoji_map(db: AsyncSession) -> dict[str, str]:
    async def loader() -> dict[str, str]:
        rows = (await db.execute(select(EmojiSetting))).scalars().all()
        return {row.key: row.value for row in rows}

    return await cache.get_or_set(NS_EMOJI, "all", loader, ttl=600)


async def e(db: AsyncSession, key: str, default: str = "") -> str:
    emoji = await get_emoji_map(db)
    if key in emoji:
        return emoji[key]
    return defaults.EMOJI.get(key, {}).get("value", default)


# =====================================================================
#  ИЗОБРАЖЕНИЯ
# =====================================================================
@dataclass(slots=True)
class ImageRef:
    key: str
    url: str | None
    tg_file_id: str | None
    is_active: bool

    @property
    def best(self) -> str | None:
        """file_id быстрее URL — Telegram не перезакачивает файл."""
        if not self.is_active:
            return None
        return self.tg_file_id or self.url or None


async def get_images(db: AsyncSession) -> dict[str, ImageRef]:
    async def loader() -> dict[str, dict[str, Any]]:
        rows = (await db.execute(select(ImageSetting))).scalars().all()
        return {
            row.key: {
                "url": row.url,
                "tg_file_id": row.tg_file_id,
                "is_active": bool(row.is_active),
            }
            for row in rows
        }

    raw = await cache.get_or_set(NS_IMAGES, "all", loader, ttl=600)
    return {
        key: ImageRef(
            key=key,
            url=item.get("url"),
            tg_file_id=item.get("tg_file_id"),
            is_active=bool(item.get("is_active", True)),
        )
        for key, item in raw.items()
    }


async def image(db: AsyncSession, key: str) -> ImageRef | None:
    images = await get_images(db)
    return images.get(key)


async def remember_file_id(db: AsyncSession, key: str, file_id: str) -> None:
    """Запомнить file_id после первой отправки картинки (ускорение)."""
    row = (
        await db.execute(select(ImageSetting).where(ImageSetting.key == key))
    ).scalar_one_or_none()
    if row is None or row.tg_file_id == file_id:
        return
    row.tg_file_id = file_id
    await db.commit()
    await cache.bump(NS_IMAGES)


# =====================================================================
#  КНОПКИ И БЛОКИ
# =====================================================================
async def get_buttons(db: AsyncSession, screen: str) -> list[dict[str, Any]]:
    async def loader() -> list[dict[str, Any]]:
        stmt = (
            select(ButtonSetting)
            .where(ButtonSetting.screen == screen, ButtonSetting.is_active.is_(True))
            .order_by(ButtonSetting.row, ButtonSetting.position, ButtonSetting.id)
        )
        rows = (await db.execute(stmt)).scalars().all()
        return [
            {
                "id": row.id,
                "code": row.code,
                "title": row.title or {},
                "emoji": row.emoji,
                "action": str(row.action),
                "url": row.url,
                "payload": row.payload,
                "row": row.row,
                "position": row.position,
                "style": row.style,
                "is_wide": bool(row.is_wide),
            }
            for row in rows
        ]

    return await cache.get_or_set(NS_BUTTONS, screen, loader, ttl=600)


async def get_blocks(db: AsyncSession, screen: str) -> list[dict[str, Any]]:
    async def loader() -> list[dict[str, Any]]:
        stmt = (
            select(DesignBlock)
            .where(DesignBlock.screen == screen, DesignBlock.is_active.is_(True))
            .order_by(DesignBlock.position, DesignBlock.id)
        )
        rows = (await db.execute(stmt)).scalars().all()
        return [
            {
                "id": row.id,
                "block_type": str(row.block_type),
                "position": row.position,
                "title": row.title or {},
                "content": row.content or {},
                "emoji": row.emoji,
                "image_url": row.image_url,
                "config": row.config or {},
            }
            for row in rows
        ]

    return await cache.get_or_set(NS_DESIGN, f"blocks:{screen}", loader, ttl=600)


# =====================================================================
#  ГЛОБАЛЬНЫЙ ДИЗАЙН
# =====================================================================
async def get_design_settings(db: AsyncSession) -> dict[str, Any]:
    async def loader() -> dict[str, Any]:
        rows = (await db.execute(select(DesignSetting))).scalars().all()
        return {row.key: row.value for row in rows}

    stored = await cache.get_or_set(NS_DESIGN, "settings", loader, ttl=600)
    merged = dict(defaults.DESIGN)
    merged.update({k: v for k, v in stored.items() if v is not None})
    return merged


async def design_value(db: AsyncSession, key: str, default: Any = None) -> Any:
    design = await get_design_settings(db)
    value = design.get(key, defaults.DESIGN.get(key, default))
    return default if value is None else value


async def set_design_value(db: AsyncSession, key: str, value: Any) -> None:
    row = (
        await db.execute(select(DesignSetting).where(DesignSetting.key == key))
    ).scalar_one_or_none()
    if row is None:
        row = DesignSetting(key=key, value=value, label=defaults.DESIGN_LABELS.get(key))
        db.add(row)
    else:
        row.value = value
    await db.commit()
    await cache.bump(NS_DESIGN)


# =====================================================================
#  СИСТЕМНЫЕ НАСТРОЙКИ
# =====================================================================
async def _settings_raw(db: AsyncSession) -> dict[str, dict[str, Any]]:
    """Сырые настройки (секреты в зашифрованном виде) — можно кэшировать."""

    async def loader() -> dict[str, dict[str, Any]]:
        rows = (await db.execute(select(SystemSetting))).scalars().all()
        return {
            row.key: {
                "value": row.value,
                "group": row.group,
                "label": row.label,
                "is_secret": bool(row.is_secret),
            }
            for row in rows
        }

    return await cache.get_or_set(NS_SETTINGS, "all", loader, ttl=300)


def _decode(entry: dict[str, Any]) -> Any:
    value = entry.get("value")
    if entry.get("is_secret") and isinstance(value, str) and value:
        try:
            return decrypt_secret(value)
        except Exception:  # pragma: no cover - повреждённый шифртекст
            log.warning("cms.secret_decrypt_failed")
            return ""
    return value


async def get_settings_group(db: AsyncSession, group: str) -> dict[str, Any]:
    """Вернуть настройки группы без префикса (payment.secret → secret)."""
    raw = await _settings_raw(db)
    prefix_map = {"telegram": "tg", "shop": "shop", "payment": "payment", "bot": "bot"}
    prefix = prefix_map.get(group, group)
    result: dict[str, Any] = {}
    for key, entry in raw.items():
        if entry.get("group") != group:
            continue
        short = key[len(prefix) + 1 :] if key.startswith(f"{prefix}.") else key
        result[short] = _decode(entry)
    # Добавляем дефолты для ключей, которых ещё нет в БД.
    for key, meta in defaults.SYSTEM.items():
        if meta["group"] != group:
            continue
        short = key[len(prefix) + 1 :] if key.startswith(f"{prefix}.") else key
        result.setdefault(short, meta["value"])
    return result


async def setting(db: AsyncSession, key: str, default: Any = None) -> Any:
    raw = await _settings_raw(db)
    entry = raw.get(key)
    if entry is not None:
        value = _decode(entry)
        return default if value in (None, "") and default is not None else value
    meta = defaults.SYSTEM.get(key)
    if meta is not None and default is None:
        return meta["value"]
    return default


async def settings_meta(db: AsyncSession, group: str | None = None) -> list[dict[str, Any]]:
    """Метаданные для форм админки. Секреты — только флаг заполненности."""
    raw = await _settings_raw(db)
    items: list[dict[str, Any]] = []
    keys: Iterable[str] = [k for k, m in defaults.SYSTEM.items() if group is None or m["group"] == group]
    for key in keys:
        meta = defaults.SYSTEM[key]
        entry = raw.get(key)
        is_secret = bool(entry["is_secret"]) if entry else bool(meta["is_secret"])
        value = _decode(entry) if entry else meta["value"]
        items.append(
            {
                "key": key,
                "group": meta["group"],
                "label": (entry.get("label") if entry else None) or meta["label"],
                "is_secret": is_secret,
                "value": "" if is_secret else value,
                "has_value": bool(value) if is_secret else True,
                "default": meta["value"],
            }
        )
    return items


async def set_setting(
    db: AsyncSession,
    key: str,
    value: Any,
    *,
    group: str = "general",
    is_secret: bool = False,
    label: str | None = None,
) -> None:
    """Создать/обновить настройку. Секреты шифруются перед записью."""
    meta = defaults.SYSTEM.get(key, {})
    group = meta.get("group", group)
    is_secret = bool(meta.get("is_secret", is_secret))
    label = label or meta.get("label")

    stored = encrypt_secret(str(value)) if (is_secret and value not in (None, "")) else value
    row = (
        await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    ).scalar_one_or_none()
    if row is None:
        row = SystemSetting(
            key=key, value=stored, group=group, label=label, is_secret=is_secret
        )
        db.add(row)
    else:
        if is_secret and value in (None, ""):
            # Пустое поле в форме = «не менять секрет».
            return
        row.value = stored
        row.group = group
        row.is_secret = is_secret
        if label:
            row.label = label
    await db.commit()
    await cache.bump(NS_SETTINGS)


async def invalidate_all() -> None:
    """Сбросить все CMS-кэши (используется после seed и массовых правок)."""
    for namespace in (NS_TEXTS, NS_EMOJI, NS_IMAGES, NS_BUTTONS, NS_DESIGN, NS_SETTINGS):
        await cache.bump(namespace)


async def format_price(db: AsyncSession, amount: Any, currency: str = "") -> str:
    """Формат цены полностью управляется из админки (price_format, currency_symbol)."""
    design = await get_design_settings(db)
    template = str(design.get("price_format") or "{amount} {symbol}")
    symbol = str(design.get("currency_symbol") or currency or "")
    try:
        number = float(amount)
    except (TypeError, ValueError):
        number = 0.0
    if abs(number - int(number)) < 0.005:
        amount_text = f"{int(number)}"
    else:
        amount_text = f"{number:.2f}"
    try:
        return template.format(amount=amount_text, symbol=symbol, currency=currency).strip()
    except (KeyError, IndexError, ValueError):
        return f"{amount_text} {symbol}".strip()
