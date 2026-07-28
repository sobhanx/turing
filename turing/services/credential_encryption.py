from __future__ import annotations

"""Encrypt / decrypt connector OAuth and API credential material."""

import logging

from django.core.exceptions import ImproperlyConfigured

from turing.security.secrets import decrypt_secret, encrypt_secret, is_encrypted

logger = logging.getLogger(__name__)


class CredentialEncryptionService:
    """
    Application-secret Fernet encryption for ``ConnectorCredential`` tokens.

    Ciphertext is written to the database; plaintext is returned only to callers
    that need it during connector execution. Never log plaintext.
    """

    def encrypt(self, plaintext: str | None) -> str:
        """Encrypt plaintext for storage. Empty input stays empty."""
        if not plaintext:
            return ""
        return encrypt_secret(str(plaintext))

    def decrypt(self, stored: str | None) -> str:
        """
        Decrypt a stored credential value.

        Raises ``ImproperlyConfigured`` if the ciphertext cannot be decrypted
        (e.g. SECRET_KEY rotated). Never logs the secret material.
        """
        if not stored:
            return ""
        try:
            return decrypt_secret(str(stored))
        except ImproperlyConfigured:
            logger.exception("Failed to decrypt connector credential")
            raise ImproperlyConfigured(
                "Unable to decrypt a connector credential. The Django SECRET_KEY "
                "may have changed since the credential was saved. Re-authorize "
                "the connector installation."
            ) from None

    def is_encrypted(self, value: str | None) -> bool:
        return is_encrypted(value)
