from __future__ import annotations

import io
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db.models.deletion import ProtectedError
from django.utils import timezone

from turing.conf import clear_settings_cache
from turing.domain.enums import JobStatus, UseCase
from turing.models import (
    Organization,
    ProcessingAttempt,
    ProcessingLog,
    ProviderCredential,
    SpeechProviderConfig,
)
from turing.security.secrets import ENCRYPTED_PREFIX, is_encrypted, mask_secret
from turing.services.credential_manager import CredentialManager
from turing.services.job_orchestrator import JobOrchestrator
from turing.services.media import MediaService
from turing.services.transcription import TranscriptionService


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_settings_cache()
    yield
    clear_settings_cache()


@pytest.fixture
def provider_a(db):
    obj, _ = SpeechProviderConfig.objects.get_or_create(
        code="speechmatics",
        defaults={"name": "Speechmatics", "is_active": True},
    )
    return obj


@pytest.fixture
def provider_b(db):
    obj, _ = SpeechProviderConfig.objects.get_or_create(
        code="other-stt",
        defaults={"name": "Other STT", "is_active": True},
    )
    return obj


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_provider_credential_api_key_encrypted(provider_a):
    cred = ProviderCredential.objects.create(
        provider=provider_a,
        name="production-primary",
        api_key="pool-secret-key-abc",
        is_active=True,
    )
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT api_key FROM turing_providercredential WHERE id = %s",
            [cred.pk],
        )
        raw = cursor.fetchone()[0]
    assert is_encrypted(raw)
    assert ENCRYPTED_PREFIX in raw
    assert "pool-secret-key-abc" not in raw

    cred.refresh_from_db()
    assert cred.api_key == "pool-secret-key-abc"
    assert cred.api_key_masked == mask_secret("pool-secret-key-abc")
    assert str(cred) == "Speechmatics key production-primary"


@pytest.mark.django_db
def test_provider_credential_save_clears_settings_cache(provider_a):
    with patch("turing.conf.clear_settings_cache") as clear:
        ProviderCredential.objects.create(
            provider=provider_a,
            name="cache-clear",
            api_key="k",
        )
        assert clear.called


# ---------------------------------------------------------------------------
# CredentialManager.acquire
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_acquire_prefers_lower_priority(provider_a):
    low = ProviderCredential.objects.create(
        provider=provider_a,
        name="low-priority",
        api_key="key-low",
        priority=20,
        is_active=True,
    )
    high = ProviderCredential.objects.create(
        provider=provider_a,
        name="high-priority",
        api_key="key-high",
        priority=10,
        is_active=True,
    )
    got = CredentialManager.acquire("speechmatics")
    assert got is not None
    assert got.pk == high.pk
    assert got.pk != low.pk
    got.refresh_from_db()
    assert got.last_used_at is not None


@pytest.mark.django_db
def test_acquire_same_priority_older_last_used_wins(provider_a):
    now = timezone.now()
    newer = ProviderCredential.objects.create(
        provider=provider_a,
        name="newer",
        api_key="key-newer",
        priority=10,
        last_used_at=now,
        is_active=True,
    )
    older = ProviderCredential.objects.create(
        provider=provider_a,
        name="older",
        api_key="key-older",
        priority=10,
        last_used_at=now - timedelta(hours=1),
        is_active=True,
    )
    got = CredentialManager.acquire("speechmatics")
    assert got is not None
    assert got.pk == older.pk
    assert got.pk != newer.pk


@pytest.mark.django_db
def test_acquire_skips_cooldown(provider_a):
    future = timezone.now() + timedelta(hours=1)
    ProviderCredential.objects.create(
        provider=provider_a,
        name="cooling",
        api_key="key-cool",
        priority=1,
        cooldown_until=future,
        is_active=True,
    )
    usable = ProviderCredential.objects.create(
        provider=provider_a,
        name="usable",
        api_key="key-ok",
        priority=50,
        is_active=True,
    )
    got = CredentialManager.acquire("speechmatics")
    assert got is not None
    assert got.pk == usable.pk


@pytest.mark.django_db
def test_acquire_skips_inactive(provider_a):
    ProviderCredential.objects.create(
        provider=provider_a,
        name="off",
        api_key="key-off",
        priority=1,
        is_active=False,
    )
    usable = ProviderCredential.objects.create(
        provider=provider_a,
        name="on",
        api_key="key-on",
        priority=50,
        is_active=True,
    )
    got = CredentialManager.acquire("speechmatics")
    assert got is not None
    assert got.pk == usable.pk


@pytest.mark.django_db
def test_acquire_returns_none_when_all_unavailable(provider_a):
    ProviderCredential.objects.create(
        provider=provider_a,
        name="off",
        api_key="a",
        is_active=False,
    )
    ProviderCredential.objects.create(
        provider=provider_a,
        name="cool",
        api_key="b",
        is_active=True,
        cooldown_until=timezone.now() + timedelta(days=1),
    )
    assert CredentialManager.acquire("speechmatics") is None


@pytest.mark.django_db
def test_acquire_provider_isolation(provider_a, provider_b):
    ProviderCredential.objects.create(
        provider=provider_a,
        name="a-key",
        api_key="key-a",
        priority=1,
        is_active=True,
    )
    b_cred = ProviderCredential.objects.create(
        provider=provider_b,
        name="b-key",
        api_key="key-b",
        priority=1,
        is_active=True,
    )
    got = CredentialManager.acquire("other-stt")
    assert got is not None
    assert got.pk == b_cred.pk
    assert CredentialManager.acquire("missing-provider") is None


@pytest.mark.django_db
def test_is_available_helper(provider_a):
    cred = ProviderCredential.objects.create(
        provider=provider_a,
        name="check",
        api_key="k",
        is_active=True,
    )
    assert CredentialManager.is_available(cred) is True
    cred.is_active = False
    assert CredentialManager.is_available(cred) is False
    cred.is_active = True
    cred.cooldown_until = timezone.now() + timedelta(minutes=5)
    assert CredentialManager.is_available(cred) is False
    cred.cooldown_until = timezone.now() - timedelta(minutes=1)
    assert CredentialManager.is_available(cred) is True


@pytest.mark.django_db
def test_attempt_protects_credential_from_delete(provider_a):
    cred = ProviderCredential.objects.create(
        provider=provider_a,
        name="linked",
        api_key="key-linked",
        is_active=True,
    )
    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="protect.wav",
        use_case=UseCase.VOICE_FILE,
        organization=Organization.get_default(),
    )
    job = JobOrchestrator().create_transcription_job(
        media=media,
        language_code="fa",
        auto_enqueue=False,
    )
    ProcessingAttempt.objects.create(
        job=job,
        attempt_number=1,
        provider_code=job.provider_code,
        status=JobStatus.RUNNING,
        provider_credential=cred,
    )
    with pytest.raises(ProtectedError):
        cred.delete()


# ---------------------------------------------------------------------------
# Phase 2 — sticky credential on Attempt creation
# ---------------------------------------------------------------------------


@pytest.fixture
def job(db, provider_a):
    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="sticky.wav",
        use_case=UseCase.VOICE_FILE,
        organization=Organization.get_default(),
    )
    return JobOrchestrator().create_transcription_job(
        media=media,
        language_code="fa",
        provider_code=provider_a.code,
        auto_enqueue=False,
    )


@pytest.mark.django_db
def test_begin_attempt_stores_acquired_credential(provider_a, job):
    cred = ProviderCredential.objects.create(
        provider=provider_a,
        name="production-primary",
        api_key="sticky-secret-xyz",
        priority=10,
        is_active=True,
    )
    attempt = JobOrchestrator().begin_attempt(job)
    assert attempt.provider_credential_id == cred.pk
    assert attempt.provider_credential == cred


@pytest.mark.django_db
def test_ensure_running_attempt_reuses_without_acquire(provider_a, job):
    cred = ProviderCredential.objects.create(
        provider=provider_a,
        name="reuse-me",
        api_key="reuse-secret",
        is_active=True,
    )
    first = JobOrchestrator().begin_attempt(job)
    assert first.provider_credential_id == cred.pk

    with patch(
        "turing.services.credential_manager.CredentialManager.acquire"
    ) as acquire:
        again = TranscriptionService()._ensure_running_attempt(job)
        acquire.assert_not_called()

    assert again.pk == first.pk
    assert again.provider_credential_id == cred.pk


@pytest.mark.django_db
def test_retry_begin_attempt_may_select_different_credential(provider_a, job):
    cred_a = ProviderCredential.objects.create(
        provider=provider_a,
        name="cred-a",
        api_key="secret-a",
        priority=10,
        is_active=True,
    )
    cred_b = ProviderCredential.objects.create(
        provider=provider_a,
        name="cred-b",
        api_key="secret-b",
        priority=20,
        is_active=True,
    )
    orch = JobOrchestrator()
    attempt1 = orch.begin_attempt(job)
    assert attempt1.provider_credential_id == cred_a.pk

    orch.mark_failed(
        job,
        attempt1,
        error_code="PROVIDER_QUOTA",
        error_message="quota",
    )
    job.refresh_from_db()
    assert job.status == JobStatus.FAILED

    # Prefer B on next acquire (A still usable but we force via mock for clarity).
    with patch(
        "turing.services.credential_manager.CredentialManager.acquire",
        return_value=cred_b,
    ):
        attempt2 = orch.begin_attempt(job)

    assert attempt2.pk != attempt1.pk
    assert attempt2.attempt_number == 2
    assert attempt2.provider_credential_id == cred_b.pk


@pytest.mark.django_db
def test_begin_attempt_empty_pool_leaves_credential_null(job):
    assert not ProviderCredential.objects.filter(
        provider__code=job.provider_code, is_active=True
    ).exists()
    attempt = JobOrchestrator().begin_attempt(job)
    assert attempt.provider_credential_id is None
    assert attempt.status == JobStatus.RUNNING


@pytest.mark.django_db
def test_begin_attempt_logs_do_not_leak_api_key(provider_a, job):
    secret = "never-log-this-api-key-value"
    ProviderCredential.objects.create(
        provider=provider_a,
        name="logged-cred",
        api_key=secret,
        is_active=True,
    )
    attempt = JobOrchestrator().begin_attempt(job)
    logs = list(ProcessingLog.objects.filter(job=job, attempt=attempt))
    assert any(
        log.message == "Provider credential selected for attempt" for log in logs
    )
    for log in logs:
        assert secret not in log.message
        assert secret not in str(log.context)
        assert "api_key" not in log.context
        blob = f"{log.message}{log.context}"
        assert "never-log" not in blob
