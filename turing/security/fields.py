from __future__ import annotations

from django.db import models

from turing.security.secrets import decrypt_secret, encrypt_secret, is_encrypted


class EncryptedCharField(models.CharField):
    """
    CharField that stores Fernet-encrypted values.

    - Reads: decrypt (legacy plaintext returned unchanged)
    - Writes: encrypt unless already prefixed
    """

    description = "Encrypted character field"

    def from_db_value(self, value, expression, connection):
        if value is None:
            return value
        return decrypt_secret(value)

    def to_python(self, value):
        if value is None:
            return value
        value = str(value)
        if is_encrypted(value):
            return decrypt_secret(value)
        return value

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value is None or value == "":
            return ""
        return encrypt_secret(str(value))
