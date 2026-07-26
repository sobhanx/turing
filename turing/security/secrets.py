"""Fernet-based secret encryption for provider credentials."""

from __future__ import annotations

import base64
import hashlib
import logging

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)

ENCRYPTED_PREFIX = "turingenc:v1:"


def _fernet():
    from cryptography.fernet import Fernet

    digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def is_encrypted(value: str | None) -> bool:
    return bool(value) and str(value).startswith(ENCRYPTED_PREFIX)


def encrypt_secret(plaintext: str | None) -> str:
    """Encrypt a secret for database storage. Empty input stays empty."""
    if not plaintext:
        return ""
    text = str(plaintext)
    if is_encrypted(text):
        return text
    token = _fernet().encrypt(text.encode("utf-8")).decode("ascii")
    return f"{ENCRYPTED_PREFIX}{token}"


def decrypt_secret(stored: str | None) -> str:
    """
    Decrypt a stored secret.

    Legacy plaintext (no prefix) is returned as-is so existing rows keep working
    until the next save re-encrypts them.
    """
    if not stored:
        return ""
    text = str(stored)
    if not is_encrypted(text):
        return text
    token = text[len(ENCRYPTED_PREFIX) :]
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to decrypt provider secret")
        raise ImproperlyConfigured(
            "Unable to decrypt a provider API key. The Django SECRET_KEY may have "
            "changed since the key was saved. Re-enter the API key in Admin "
            "(Speech provider configs) or set TURING_SPEECHMATICS_API_KEY."
        ) from exc


def mask_secret(secret: str | None) -> str:
    """Admin-safe display, e.g. ********abcd."""
    if not secret:
        return "(not set)"
    text = str(secret)
    if len(text) <= 4:
        return "********"
    return f"********{text[-4:]}"
