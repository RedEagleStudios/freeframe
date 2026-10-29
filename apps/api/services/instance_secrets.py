"""Encryption at rest for instance-level secrets (e.g. the Discord client secret).

Fernet (AES-128-CBC + HMAC-SHA256) with a key derived from JWT_SECRET under its
own label, so it never equals the share-password key in crypto_service.
Rotating JWT_SECRET makes stored secrets unreadable: decrypt_secret then
returns None (treated as "not set") and the admin re-enters the secret.
"""
import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from ..config import settings

logger = logging.getLogger(__name__)

_KEY_LABEL = b"freeframe:instance-secrets:v1:"


def _fernet() -> Fernet:
    digest = hashlib.sha256(_KEY_LABEL + settings.jwt_secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(token: str | None) -> str | None:
    """The plain secret, or None when unset or no longer decryptable."""
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        logger.warning(
            "A stored instance secret could not be decrypted (was JWT_SECRET "
            "changed?). Treating it as unset; re-enter it in the admin settings."
        )
        return None
