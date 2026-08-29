import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings

_hasher = PasswordHasher()

# Computed once at import time so verify_password() always performs a real
# Argon2 verification, even when no user/hash exists for the given email —
# keeping login/forgot-password/resend-verification roughly constant-time
# and free of an early-return timing tell (no user enumeration).
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(32))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


ACCESS_TOKEN_TYPE = "access"


class InvalidAccessTokenError(Exception):
    pass


def create_access_token(*, user_id: str, is_super_admin: bool, token_version: int) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "jti": secrets.token_hex(16),
        "type": ACCESS_TOKEN_TYPE,
        "is_super_admin": is_super_admin,
        "token_version": token_version,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_ttl_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise InvalidAccessTokenError(str(exc)) from exc
    if payload.get("type") != ACCESS_TOKEN_TYPE:
        raise InvalidAccessTokenError("unexpected token type")
    return payload


def generate_opaque_token() -> str:
    """Raw, single-use secret for refresh/reset/verification tokens."""
    return secrets.token_urlsafe(48)


def generate_temporary_password() -> str:
    """One-time login password for an admin-provisioned user, emailed to
    them directly. token_urlsafe(12) comfortably clears PasswordStr's
    10-char minimum after base64 encoding."""
    return secrets.token_urlsafe(12)


def generate_otp() -> str:
    """6-digit numeric forgot-password code, cryptographically random
    (secrets.randbelow, not the `random` module) and zero-padded so it's
    always exactly 6 digits (e.g. 42 -> "000042")."""
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_opaque_token(raw_token: str) -> str:
    """SHA-256 hex digest — the only form these opaque tokens are ever stored in."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
