"""Пароли (Argon2id), шифрование секретов в БД, CSRF-токены, роли."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from enum import StrEnum

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)


# ---------------- Пароли ----------------
def hash_password(raw: str) -> str:
    return _hasher.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, raw)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def needs_rehash(hashed: str) -> bool:
    try:
        return _hasher.check_needs_rehash(hashed)
    except (InvalidHashError, ValueError):
        return False


# ---------------- Шифрование секретов, хранимых в БД ----------------
def _fernet() -> Fernet:
    key = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(plain: str) -> str:
    if not plain:
        return ""
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""


def mask_secret(value: str, visible: int = 4) -> str:
    """Маскировка для UI: секреты никогда не отдаются в frontend целиком."""
    if not value:
        return ""
    if len(value) <= visible * 2:
        return "•" * 8
    return f"{value[:visible]}{'•' * 12}{value[-visible:]}"


# ---------------- CSRF ----------------
def generate_password(length: int = 16) -> str:
    """Генератор паролей для новых администраторов."""
    alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    size = max(8, int(length))
    return "".join(secrets.choice(alphabet) for _ in range(size))


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_ok(sent: str | None, expected: str | None) -> bool:
    if not sent or not expected:
        return False
    return hmac.compare_digest(sent, expected)


# ---------------- Подпись webhook ----------------
def hmac_sha256_hex(secret: str, payload: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def signature_ok(secret: str, payload: bytes, provided: str | None) -> bool:
    if not provided:
        return False
    expected = hmac_sha256_hex(secret, payload)
    return hmac.compare_digest(expected, provided.strip().lower())


# ---------------- Роли и права (ТЗ п.36) ----------------
class AdminRole(StrEnum):
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    MANAGER = "manager"


class Perm(StrEnum):
    DASHBOARD = "dashboard"
    CATALOG = "catalog"
    INVENTORY = "inventory"
    ORDERS = "orders"
    PAYMENTS_VIEW = "payments_view"
    PAYMENTS_CONFIG = "payments_config"
    USERS = "users"
    PROMO = "promo"
    CMS = "cms"  # дизайн, тексты, кнопки, emoji, изображения
    BROADCAST = "broadcast"
    SETTINGS = "settings"
    ADMINS = "admins"
    AUDIT = "audit"


ROLE_PERMISSIONS: dict[AdminRole, frozenset[Perm]] = {
    AdminRole.SUPER_ADMIN: frozenset(Perm),
    AdminRole.ADMIN: frozenset(
        {
            Perm.DASHBOARD,
            Perm.CATALOG,
            Perm.INVENTORY,
            Perm.ORDERS,
            Perm.PAYMENTS_VIEW,
            Perm.USERS,
            Perm.PROMO,
            Perm.CMS,
            Perm.BROADCAST,
            Perm.AUDIT,
        }
    ),
    AdminRole.MANAGER: frozenset(
        {Perm.DASHBOARD, Perm.CATALOG, Perm.INVENTORY, Perm.ORDERS}
    ),
}


def role_has(role: str | AdminRole, perm: Perm) -> bool:
    try:
        role_enum = AdminRole(role)
    except ValueError:
        return False
    return perm in ROLE_PERMISSIONS[role_enum]
