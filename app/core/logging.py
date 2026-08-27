"""Структурное логирование. Секреты никогда не попадают в логи (см. _redact)."""
from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from app.core.config import settings

SENSITIVE_KEYS = {
    "bot_token",
    "token",
    "password",
    "passwd",
    "secret",
    "x-secret",
    "api_key",
    "apikey",
    "authorization",
    "platega_secret",
    "platega_webhook_secret",
    "merchant_id",
    "x-merchantid",
    "password_hash",
    "session",
    "cookie",
}
MASK = "***REDACTED***"


def _redact(_logger: Any, _name: str, event_dict: dict) -> dict:
    for key in list(event_dict.keys()):
        if key.lower() in SENSITIVE_KEYS:
            event_dict[key] = MASK
        elif isinstance(event_dict[key], dict):
            event_dict[key] = {
                k: (MASK if k.lower() in SENSITIVE_KEYS else v)
                for k, v in event_dict[key].items()
            }
    return event_dict


def setup_logging() -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)

    renderer = (
        structlog.processors.JSONRenderer()
        if settings.is_production
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    for noisy in ("aiogram.event", "httpx", "httpcore", "sqlalchemy.engine.Engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)
