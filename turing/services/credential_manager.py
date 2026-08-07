from __future__ import annotations

"""Acquire pool credentials for STT providers (no HTTP / adapter logic)."""

from django.db import connection, transaction
from django.db.models import F, Q
from django.utils import timezone

from turing.models.configuration import ProviderCredential


class CredentialManager:
    """
    Select an available ``ProviderCredential`` for a provider code.

    Does not perform provider HTTP calls. Failure/cooldown marking belongs to
    a later phase.
    """

    @classmethod
    def is_available(cls, credential: ProviderCredential) -> bool:
        """Return True when the credential may be selected for new work."""
        if not credential.is_active:
            return False
        if credential.cooldown_until is None:
            return True
        return credential.cooldown_until <= timezone.now()

    @classmethod
    def acquire(cls, provider_code: str) -> ProviderCredential | None:
        """
        Atomically pick the next available credential for ``provider_code``.

        Updates ``last_used_at`` on success. Returns ``None`` when the pool has
        no usable rows (caller may fall back to legacy config/env later).
        """
        code = (provider_code or "").strip()
        if not code:
            return None

        now = timezone.now()
        with transaction.atomic():
            qs = (
                ProviderCredential.objects.select_related("provider")
                .filter(
                    provider__code=code,
                    is_active=True,
                )
                .filter(Q(cooldown_until__isnull=True) | Q(cooldown_until__lte=now))
                .order_by(
                    "priority",
                    F("last_used_at").asc(nulls_first=True),
                    "id",
                )
            )
            if connection.vendor == "postgresql":
                qs = qs.select_for_update(skip_locked=True)
            else:
                qs = qs.select_for_update()

            credential = qs.first()
            if credential is None:
                return None

            credential.last_used_at = now
            credential.save(update_fields=["last_used_at", "updated_at"])
            return credential
