from __future__ import annotations

"""Acquire pool credentials for STT providers (no HTTP / adapter logic)."""

from dataclasses import dataclass
from datetime import timedelta
from enum import Enum

from django.db import connection, transaction
from django.db.models import F, Q
from django.utils import timezone

from turing.models.configuration import ProviderCredential
from turing.services.credential_signals import record_credential_event

# Cooldown durations for pool failures (production defaults).
COOLDOWN_QUOTA_SECONDS = 15 * 60
COOLDOWN_AUTH_SECONDS = 24 * 60 * 60

# Error codes that take a credential out of the pool temporarily.
COOLDOWN_ERROR_CODES = frozenset({"PROVIDER_QUOTA", "PROVIDER_AUTH"})


class AcquireOutcome(str, Enum):
    """Result of a pool acquire attempt (distinct from legacy fallback policy)."""

    ACQUIRED = "acquired"
    EMPTY_POOL = "empty_pool"
    POOL_EXHAUSTED = "pool_exhausted"


@dataclass(frozen=True)
class AcquireResult:
    """
    Explicit acquire outcome.

    - ``ACQUIRED``: ``credential`` is set.
    - ``EMPTY_POOL``: no ``ProviderCredential`` rows for the provider code
      (legacy singleton fallback is the compatibility path).
    - ``POOL_EXHAUSTED``: rows exist but none are available (inactive/cooldown).
      Legacy fallback may still apply for compatibility, but must be logged —
      exhaustion is not silent.
    """

    credential: ProviderCredential | None
    outcome: AcquireOutcome
    provider_code: str = ""

    @property
    def ok(self) -> bool:
        return self.credential is not None


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

        Convenience wrapper around :meth:`acquire_result` that returns only the
        credential (or ``None``). Prefer ``acquire_result`` when callers need
        empty-pool vs exhausted distinction.
        """
        return cls.acquire_result(provider_code).credential

    @classmethod
    def acquire_result(cls, provider_code: str) -> AcquireResult:
        """
        Atomically pick the next available credential, with explicit outcome.

        Ordering: ``priority`` ASC, ``last_used_at`` ASC NULLS FIRST, ``id`` ASC.
        On PostgreSQL uses ``SELECT … FOR UPDATE SKIP LOCKED`` so concurrent
        workers can claim different rows without blocking.

        Updates ``last_used_at`` on success.

        Outcomes:
        - ``ACQUIRED`` — usable row selected.
        - ``EMPTY_POOL`` — no credential rows configured for this provider.
        - ``POOL_EXHAUSTED`` — rows exist but all inactive or in cooldown.
        """
        code = (provider_code or "").strip()
        if not code:
            result = AcquireResult(
                credential=None,
                outcome=AcquireOutcome.EMPTY_POOL,
                provider_code=code,
            )
            cls._emit_acquire_signals(result)
            return result

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
            if credential is not None:
                credential.last_used_at = now
                credential.save(update_fields=["last_used_at", "updated_at"])
                result = AcquireResult(
                    credential=credential,
                    outcome=AcquireOutcome.ACQUIRED,
                    provider_code=code,
                )
                cls._emit_acquire_signals(result)
                return result

            configured = ProviderCredential.objects.filter(provider__code=code).exists()
            outcome = (
                AcquireOutcome.POOL_EXHAUSTED
                if configured
                else AcquireOutcome.EMPTY_POOL
            )
            result = AcquireResult(
                credential=None,
                outcome=outcome,
                provider_code=code,
            )
            cls._emit_acquire_signals(result)
            return result

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
            record_credential_event(
                "credential_cooldown",
                credential_id=str(locked.pk),
                credential_name=locked.name,
                provider_code=getattr(locked.provider, "code", "") or "",
                error_code=code,
                failure_count=int(locked.failure_count or 0),
                cooldown_until=locked.cooldown_until.isoformat()
                if locked.cooldown_until
                else "",
            )

    @classmethod
    def _emit_acquire_signals(cls, result: AcquireResult) -> None:
        code = result.provider_code
        if result.outcome == AcquireOutcome.ACQUIRED and result.credential is not None:
            cred = result.credential
            record_credential_event(
                "credential_acquired",
                credential_id=str(cred.pk),
                credential_name=cred.name,
                provider_code=code,
            )
            return
        if result.outcome == AcquireOutcome.EMPTY_POOL:
            record_credential_event(
                "acquire_miss_empty_pool",
                provider_code=code,
            )
            return
        if result.outcome == AcquireOutcome.POOL_EXHAUSTED:
            record_credential_event(
                "acquire_miss_pool_exhausted",
                provider_code=code,
            )
