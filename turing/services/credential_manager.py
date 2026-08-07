from __future__ import annotations

"""Acquire pool credentials for STT providers (no HTTP / adapter logic)."""

from datetime import timedelta

from django.db import connection, transaction
from django.db.models import F, Q
from django.utils import timezone

from turing.models.configuration import ProviderCredential

# Cooldown durations for pool failures (production defaults).
COOLDOWN_QUOTA_SECONDS = 15 * 60
COOLDOWN_AUTH_SECONDS = 24 * 60 * 60

# Error codes that take a credential out of the pool temporarily.
COOLDOWN_ERROR_CODES = frozenset({"PROVIDER_QUOTA", "PROVIDER_AUTH"})


class CredentialManager:
    """
    Select and cool down ``ProviderCredential`` rows for a provider code.

    Invariant: a credential is selected once per ``ProcessingAttempt`` (in
    ``JobOrchestrator.begin_attempt``) and remains sticky for submit, poll,
    fetch, and cancel. Rotation happens only by creating a new Attempt.

    Does not perform provider HTTP calls. Acquisition uses a short DB
    transaction only — never hold locks across provider I/O.
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

        Ordering: ``priority`` ASC, ``last_used_at`` ASC NULLS FIRST, ``id`` ASC.
        On PostgreSQL uses ``SELECT … FOR UPDATE SKIP LOCKED`` so concurrent
        workers can claim different rows without blocking.

        Updates ``last_used_at`` on success. Returns ``None`` when the pool has
        no usable rows (caller may fall back to legacy config/env).
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

    @classmethod
    def mark_failure(
        cls,
        credential: ProviderCredential | None,
        error_code: str,
        *,
        cooldown_seconds: float | None = None,
    ) -> None:
        """
        Record a pool failure and apply cooldown when appropriate.

        - ``PROVIDER_QUOTA``: temporary cooldown (default 15 minutes).
        - ``PROVIDER_AUTH``: longer cooldown (default 24 hours); operators may
          also deactivate the row in Admin.
        - Other / transient codes: no cooldown (no-op aside from ignoring).

        Never shortens an existing longer ``cooldown_until``.
        """
        if credential is None:
            return
        code = (error_code or "").strip()
        if code not in COOLDOWN_ERROR_CODES:
            return

        if cooldown_seconds is None:
            seconds = (
                COOLDOWN_AUTH_SECONDS
                if code == "PROVIDER_AUTH"
                else COOLDOWN_QUOTA_SECONDS
            )
        else:
            seconds = float(cooldown_seconds)

        now = timezone.now()
        proposed_until = now + timedelta(seconds=max(0.0, seconds))

        with transaction.atomic():
            locked = (
                ProviderCredential.objects.select_for_update()
                .filter(pk=credential.pk)
                .first()
            )
            if locked is None:
                return
            locked.failure_count = int(locked.failure_count or 0) + 1
            locked.last_error_code = code
            locked.last_error_at = now
            if locked.cooldown_until is None or locked.cooldown_until < proposed_until:
                locked.cooldown_until = proposed_until
            locked.save(
                update_fields=[
                    "failure_count",
                    "last_error_code",
                    "last_error_at",
                    "cooldown_until",
                    "updated_at",
                ]
            )
