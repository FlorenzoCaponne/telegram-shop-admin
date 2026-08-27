"""Redis-кэш для CMS-настроек (тексты, emoji, изображения, кнопки, дизайн, каталог).

Ключевая идея: админка меняет данные → бамп версии неймспейса в Redis → все процессы
(бот, API) мгновенно видят новые данные без перезапуска (ТЗ п.47, п.59).
Сверху — L1 in-process кэш с коротким TTL, чтобы handlers отвечали мгновенно.
"""
from __future__ import annotations

import time
from typing import Any

import orjson
import redis.asyncio as aioredis

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

# Неймспейсы кэша — инвалидируются целиком при изменении в админке.
NS_TEXTS = "texts"
NS_EMOJI = "emoji"
NS_IMAGES = "images"
NS_BUTTONS = "buttons"
NS_DESIGN = "design"
NS_CATALOG = "catalog"
NS_SETTINGS = "settings"
ALL_NAMESPACES = (
    NS_TEXTS,
    NS_EMOJI,
    NS_IMAGES,
    NS_BUTTONS,
    NS_DESIGN,
    NS_CATALOG,
    NS_SETTINGS,
)

_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            settings.redis_url,
            encoding=None,
            decode_responses=False,
            socket_timeout=2,
            socket_connect_timeout=2,
            health_check_interval=30,
            max_connections=50,
        )
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
    _redis = None


class Cache:
    """Двухуровневый кэш с версионированием неймспейсов.

    Все ошибки Redis подавляются: недоступный кэш не должен ронять бота,
    мы просто читаем из PostgreSQL.
    """

    L1_TTL = 3.0  # секунды

    def __init__(self) -> None:
        self._l1: dict[str, tuple[float, Any]] = {}
        self._versions: dict[str, tuple[float, int]] = {}

    # ---------- версии неймспейсов ----------
    @staticmethod
    def _ver_key(ns: str) -> str:
        return f"cms:ver:{ns}"

    async def version(self, ns: str) -> int:
        cached = self._versions.get(ns)
        now = time.monotonic()
        if cached and now - cached[0] < self.L1_TTL:
            return cached[1]
        value = 1
        try:
            raw = await get_redis().get(self._ver_key(ns))
            if raw is not None:
                value = int(raw)
        except Exception as exc:  # noqa: BLE001
            log.warning("cache.version_failed", ns=ns, error=str(exc))
        self._versions[ns] = (now, value)
        return value

    async def bump(self, *namespaces: str) -> None:
        """Инвалидация. Вызывается после любого сохранения в админке."""
        targets = namespaces or ALL_NAMESPACES
        try:
            redis = get_redis()
            pipe = redis.pipeline()
            for ns in targets:
                pipe.incr(self._ver_key(ns))
            await pipe.execute()
        except Exception as exc:  # noqa: BLE001
            log.warning("cache.bump_failed", error=str(exc))
        for ns in targets:
            self._versions.pop(ns, None)
        self._l1.clear()

    # ---------- значения ----------
    async def get(self, ns: str, key: str) -> Any | None:
        ver = await self.version(ns)
        full = f"cms:{ns}:v{ver}:{key}"

        hit = self._l1.get(full)
        now = time.monotonic()
        if hit and now - hit[0] < self.L1_TTL:
            return hit[1]

        try:
            raw = await get_redis().get(full)
        except Exception as exc:  # noqa: BLE001
            log.warning("cache.get_failed", key=full, error=str(exc))
            return None
        if raw is None:
            return None
        try:
            value = orjson.loads(raw)
        except orjson.JSONDecodeError:
            return None
        self._l1[full] = (now, value)
        return value

    async def set(self, ns: str, key: str, value: Any, ttl: int | None = None) -> None:
        ver = await self.version(ns)
        full = f"cms:{ns}:v{ver}:{key}"
        self._l1[full] = (time.monotonic(), value)
        try:
            await get_redis().set(
                full, orjson.dumps(value), ex=ttl or settings.cache_ttl
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("cache.set_failed", key=full, error=str(exc))

    async def get_or_set(self, ns: str, key: str, loader, ttl: int | None = None) -> Any:
        """loader — async callable без аргументов, возвращающий JSON-сериализуемое."""
        cached = await self.get(ns, key)
        if cached is not None:
            return cached
        value = await loader()
        if value is not None:
            await self.set(ns, key, value, ttl)
        return value

    # ---------- атомарный замок (защита от двойной обработки) ----------
    async def acquire_once(self, key: str, ttl: int = 3600) -> bool:
        """True, если ключ взят впервые. Используется для идемпотентности webhook."""
        try:
            return bool(await get_redis().set(f"once:{key}", b"1", ex=ttl, nx=True))
        except Exception as exc:  # noqa: BLE001
            log.warning("cache.lock_failed", key=key, error=str(exc))
            return True  # fail-open: основная защита всё равно в PostgreSQL (UNIQUE)


cache = Cache()
