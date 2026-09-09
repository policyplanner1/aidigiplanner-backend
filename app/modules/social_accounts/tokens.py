import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


def _fernet() -> Fernet:
    # Same 32-byte key every process start, derived from JWT_SECRET so we
    # don't need a second secret in .env. Rotating JWT_SECRET will make
    # existing social tokens unreadable — reconnect the account.
    digest = hashlib.sha256(get_settings().jwt_secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Could not decrypt social account token.") from exc
