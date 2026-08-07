from __future__ import annotations

import io
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db.models.deletion import ProtectedError
from django.utils import timezone

from turing.conf import clear_settings_cache, get_turing_settings
from turing.domain.enums import JobStatus, UseCase
from turing.models import (
    Organization,
    ProcessingAttempt,
    ProcessingLog,
    ProviderCredential,
    SpeechProviderConfig,
)
from turing.models.webhook import WebhookDeliveryOutcome
from turing.providers.speechmatics.client import SpeechmaticsClient
from turing.security.secrets import ENCRYPTED_PREFIX, is_encrypted, mask_secret
from turing.services.credential_manager import (
    AcquireOutcome,
    AcquireResult,
    CredentialManager,
)
from turing.services.credential_signals import (
    credential_signal_counts,
    reset_credential_signals,
)
from turing.services.job_orchestrator import JobOrchestrator
from turing.services.media import MediaService
from turing.services.transcription import TranscriptionService
from turing.webhooks.types import ProviderNotification


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_settings_cache()
    reset_credential_signals()
    yield
    clear_settings_cache()
    reset_credential_signals()


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
    from turing.services.credential_manager import AcquireOutcome, AcquireResult

    with patch(
        "turing.services.credential_manager.CredentialManager.acquire_result",
        return_value=AcquireResult(
            credential=cred_b,
            outcome=AcquireOutcome.ACQUIRED,
            provider_code="speechmatics",
        ),
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


# ---------------------------------------------------------------------------
# Phase 3 — sticky credential on submit
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_submit_uses_attempt_credential_not_singleton(provider_a, job, settings):
    settings.TURING_SPEECHMATICS_API_KEY = "legacy-env-key-should-not-win"
    provider_a.api_key = "legacy-db-key-should-not-win"
    provider_a.save()
    clear_settings_cache()
    # Warm process settings cache with the legacy key.
    assert get_turing_settings().speechmatics_api_key == "legacy-db-key-should-not-win"

    pool_secret = "pool-sticky-submit-key"
    ProviderCredential.objects.create(
        provider=provider_a,
        name="pool-submit",
        api_key=pool_secret,
        priority=1,
        is_active=True,
    )

    constructed: list[str] = []
    real_init = SpeechmaticsClient.__init__

    def tracking_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        constructed.append(self.api_key)

    with patch.object(SpeechmaticsClient, "__init__", tracking_init), patch.object(
        SpeechmaticsClient,
        "submit_job",
        return_value={"job": {"id": "ext-sticky-submit-1"}},
    ):
        result = TranscriptionService().submit(str(job.id))

    assert result == "submitted"
    assert constructed
    assert constructed[0] == pool_secret
    assert "legacy" not in constructed[0]

    job.refresh_from_db()
    attempt = job.attempts.get()
    assert attempt.provider_credential is not None
    assert attempt.provider_credential.api_key == pool_secret
    for log in ProcessingLog.objects.filter(job=job):
        assert pool_secret not in log.message
        assert pool_secret not in str(log.context)


@pytest.mark.django_db
def test_submit_null_credential_uses_legacy_singleton(provider_a, job, settings):
    settings.TURING_SPEECHMATICS_API_KEY = "env-legacy-only"
    provider_a.api_key = ""
    provider_a.save()
    clear_settings_cache()
    assert not ProviderCredential.objects.filter(provider=provider_a).exists()

    constructed: list[str] = []
    real_init = SpeechmaticsClient.__init__

    def tracking_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        constructed.append(self.api_key)

    with patch.object(SpeechmaticsClient, "__init__", tracking_init), patch.object(
        SpeechmaticsClient,
        "submit_job",
        return_value={"job": {"id": "ext-legacy-1"}},
    ):
        result = TranscriptionService().submit(str(job.id))

    assert result == "submitted"
    assert constructed == ["env-legacy-only"]
    attempt = job.attempts.get()
    assert attempt.provider_credential_id is None


@pytest.mark.django_db
def test_submit_reuses_running_attempt_without_second_acquire(provider_a, job):
    ProviderCredential.objects.create(
        provider=provider_a,
        name="once",
        api_key="only-once-key",
        is_active=True,
    )
    orch = JobOrchestrator()
    first = orch.begin_attempt(job)

    with patch(
        "turing.services.credential_manager.CredentialManager.acquire"
    ) as acquire, patch.object(
        SpeechmaticsClient,
        "submit_job",
        return_value={"job": {"id": "ext-reuse-1"}},
    ):
        result = TranscriptionService().submit(str(job.id))
        acquire.assert_not_called()

    assert result == "submitted"
    job.refresh_from_db()
    assert job.attempts.count() == 1
    assert job.attempts.get().pk == first.pk
    assert job.attempts.get().provider_credential_id == first.provider_credential_id


# ---------------------------------------------------------------------------
# Phase 4 — sticky credential on poll / fetch / cancel
# ---------------------------------------------------------------------------


def _submit_job_with_pool_key(job, provider_a, *, pool_secret: str, external_id: str):
    ProviderCredential.objects.create(
        provider=provider_a,
        name=f"cred-{external_id}",
        api_key=pool_secret,
        priority=1,
        is_active=True,
    )
    with patch.object(
        SpeechmaticsClient,
        "submit_job",
        return_value={"job": {"id": external_id}},
    ):
        assert TranscriptionService().submit(str(job.id)) == "submitted"
    job.refresh_from_db()
    return job.attempts.get()


@pytest.mark.django_db
def test_poll_uses_attempt_credential_not_singleton(provider_a, job, settings):
    settings.TURING_SPEECHMATICS_API_KEY = "legacy-poll-env"
    provider_a.api_key = "legacy-poll-db"
    provider_a.save()
    clear_settings_cache()
    get_turing_settings()

    pool_secret = "pool-sticky-poll-key"
    attempt = _submit_job_with_pool_key(
        job, provider_a, pool_secret=pool_secret, external_id="ext-poll-1"
    )

    constructed: list[str] = []
    real_init = SpeechmaticsClient.__init__

    def tracking_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        constructed.append(self.api_key)

    with patch.object(SpeechmaticsClient, "__init__", tracking_init), patch.object(
        SpeechmaticsClient,
        "get_job",
        return_value={"job": {"id": "ext-poll-1", "status": "running"}},
    ):
        outcome = TranscriptionService().poll_once(str(job.id), poll_count=0)

    assert outcome.action.value == "reschedule"
    assert constructed == [pool_secret]
    attempt.refresh_from_db()
    assert attempt.provider_credential.api_key == pool_secret


@pytest.mark.django_db
def test_fetch_uses_attempt_credential_not_singleton(provider_a, job, settings):
    settings.TURING_SPEECHMATICS_API_KEY = "legacy-fetch-env"
    provider_a.api_key = "legacy-fetch-db"
    provider_a.save()
    clear_settings_cache()
    get_turing_settings()

    pool_secret = "pool-sticky-fetch-key"
    _submit_job_with_pool_key(
        job, provider_a, pool_secret=pool_secret, external_id="ext-fetch-1"
    )

    constructed: list[str] = []
    real_init = SpeechmaticsClient.__init__

    def tracking_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        constructed.append(self.api_key)

    transcript_payload = {
        "format": "2.9",
        "job": {"id": "ext-fetch-1"},
        "metadata": {"transcript_language": "fa"},
        "results": [
            {
                "type": "word",
                "start_time": 0.0,
                "end_time": 0.5,
                "alternatives": [{"content": "سلام", "confidence": 0.9, "speaker": "S1"}],
            }
        ],
    }
    with patch.object(SpeechmaticsClient, "__init__", tracking_init), patch.object(
        SpeechmaticsClient,
        "get_transcript",
        return_value=transcript_payload,
    ):
        transcript = TranscriptionService().fetch_and_persist(str(job.id))

    assert transcript.full_text
    assert constructed == [pool_secret]


@pytest.mark.django_db
def test_cancel_uses_attempt_credential(provider_a, job, settings):
    settings.TURING_SPEECHMATICS_API_KEY = "legacy-cancel-env"
    provider_a.api_key = "legacy-cancel-db"
    provider_a.save()
    clear_settings_cache()
    get_turing_settings()

    pool_secret = "pool-sticky-cancel-key"
    _submit_job_with_pool_key(
        job, provider_a, pool_secret=pool_secret, external_id="ext-cancel-1"
    )

    constructed: list[str] = []
    real_init = SpeechmaticsClient.__init__

    def tracking_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        constructed.append(self.api_key)

    with patch.object(SpeechmaticsClient, "__init__", tracking_init), patch.object(
        SpeechmaticsClient,
        "delete_job",
        return_value=None,
    ) as delete_job:
        JobOrchestrator().cancel(job)

    assert constructed == [pool_secret]
    delete_job.assert_called()
    job.refresh_from_db()
    assert job.status == JobStatus.CANCELLED


@pytest.mark.django_db
def test_webhook_ready_fetch_uses_attempt_credential(provider_a, job, settings):
    settings.TURING_SPEECHMATICS_API_KEY = "legacy-webhook-env"
    provider_a.api_key = "legacy-webhook-db"
    provider_a.save()
    clear_settings_cache()
    get_turing_settings()

    pool_secret = "pool-sticky-webhook-key"
    attempt = _submit_job_with_pool_key(
        job, provider_a, pool_secret=pool_secret, external_id="ext-wh-1"
    )

    notification = ProviderNotification(
        provider_code="speechmatics",
        external_job_id="ext-wh-1",
        status_param="success",
        provider_state="succeeded",
        provider_message="success",
        dedupe_key="dedupe-sticky-wh",
        payload_hash="hash1",
        raw_metadata={},
    )

    constructed: list[str] = []
    real_init = SpeechmaticsClient.__init__

    def tracking_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        constructed.append(self.api_key)

    transcript_payload = {
        "format": "2.9",
        "job": {"id": "ext-wh-1"},
        "metadata": {"transcript_language": "fa"},
        "results": [
            {
                "type": "word",
                "start_time": 0.0,
                "end_time": 0.4,
                "alternatives": [{"content": "hi", "confidence": 0.9, "speaker": "S1"}],
            }
        ],
    }

    service = TranscriptionService()
    with patch(
        "turing.tasks.transcription.fetch_and_persist_transcription.delay",
        side_effect=lambda jid: service.fetch_and_persist(jid),
    ), patch.object(SpeechmaticsClient, "__init__", tracking_init), patch.object(
        SpeechmaticsClient,
        "get_transcript",
        return_value=transcript_payload,
    ):
        outcome = service.ingest_provider_notification(notification)

    assert outcome == WebhookDeliveryOutcome.PROCESSED
    assert constructed == [pool_secret]
    attempt.refresh_from_db()
    assert attempt.provider_credential.api_key == pool_secret


@pytest.mark.django_db
def test_external_job_id_resolves_correct_attempt_among_many(provider_a, job):
    cred_old = ProviderCredential.objects.create(
        provider=provider_a,
        name="old",
        api_key="old-attempt-key",
        priority=1,
        is_active=True,
    )
    cred_new = ProviderCredential.objects.create(
        provider=provider_a,
        name="new",
        api_key="new-attempt-key",
        priority=2,
        is_active=True,
    )
    orch = JobOrchestrator()
    with patch(
        "turing.services.credential_manager.CredentialManager.acquire_result",
        return_value=AcquireResult(
            credential=cred_old,
            outcome=AcquireOutcome.ACQUIRED,
            provider_code="speechmatics",
        ),
    ):
        a1 = orch.begin_attempt(job)
    a1.external_job_id = "ext-old"
    a1.status = JobStatus.FAILED
    a1.save(update_fields=["external_job_id", "status", "updated_at"])
    orch.mark_failed(job, a1, error_code="PROVIDER_QUOTA", error_message="q")

    job.refresh_from_db()
    with patch(
        "turing.services.credential_manager.CredentialManager.acquire_result",
        return_value=AcquireResult(
            credential=cred_new,
            outcome=AcquireOutcome.ACQUIRED,
            provider_code="speechmatics",
        ),
    ):
        a2 = orch.begin_attempt(job)
    a2.external_job_id = "ext-new"
    a2.save(update_fields=["external_job_id", "updated_at"])
    job.external_job_id = "ext-new"
    job.save(update_fields=["external_job_id", "updated_at"])

    service = TranscriptionService()
    resolved = service._attempt_for_provider_job(job, "ext-new")
    assert resolved is not None
    assert resolved.pk == a2.pk
    assert resolved.provider_credential_id == cred_new.pk

    constructed: list[str] = []
    real_init = SpeechmaticsClient.__init__

    def tracking_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        constructed.append(self.api_key)

    with patch.object(SpeechmaticsClient, "__init__", tracking_init), patch.object(
        SpeechmaticsClient,
        "get_job",
        return_value={"job": {"id": "ext-new", "status": "running"}},
    ):
        TranscriptionService().poll_once(str(job.id))

    assert constructed == ["new-attempt-key"]


@pytest.mark.django_db
def test_poll_legacy_null_credential_uses_fallback(provider_a, job, settings):
    settings.TURING_SPEECHMATICS_API_KEY = "env-poll-legacy"
    provider_a.api_key = ""
    provider_a.save()
    clear_settings_cache()

    with patch.object(
        SpeechmaticsClient,
        "submit_job",
        return_value={"job": {"id": "ext-leg-poll"}},
    ):
        TranscriptionService().submit(str(job.id))
    attempt = job.attempts.get()
    assert attempt.provider_credential_id is None

    constructed: list[str] = []
    real_init = SpeechmaticsClient.__init__

    def tracking_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        constructed.append(self.api_key)

    with patch.object(SpeechmaticsClient, "__init__", tracking_init), patch.object(
        SpeechmaticsClient,
        "get_job",
        return_value={"job": {"id": "ext-leg-poll", "status": "running"}},
    ):
        TranscriptionService().poll_once(str(job.id))

    assert constructed == ["env-poll-legacy"]


@pytest.mark.django_db
def test_poll_transient_error_keeps_same_attempt_credential(provider_a, job):
    from turing.domain.exceptions import ProviderError
    from turing.domain.pipeline import PollAction

    pool_secret = "pool-no-rotate-key"
    attempt = _submit_job_with_pool_key(
        job, provider_a, pool_secret=pool_secret, external_id="ext-no-rotate"
    )
    cred_id = attempt.provider_credential_id

    with patch.object(
        SpeechmaticsClient,
        "get_job",
        side_effect=ProviderError(
            "temporarily unavailable",
            code="PROVIDER_SERVER",
            retryable=True,
            provider_code="speechmatics",
        ),
    ):
        outcome = TranscriptionService().poll_once(str(job.id), poll_count=0)

    assert outcome.action == PollAction.RESCHEDULE
    attempt.refresh_from_db()
    assert attempt.provider_credential_id == cred_id
    assert job.attempts.filter(status=JobStatus.RUNNING).count() == 1
    with patch(
        "turing.services.credential_manager.CredentialManager.acquire"
    ) as acquire:
        TranscriptionService()._ensure_running_attempt(job)
        acquire.assert_not_called()


# ---------------------------------------------------------------------------
# Phase 5 — pool hardening
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_acquire_same_priority_rotates_by_last_used_at(provider_a):
    now = timezone.now()
    first = ProviderCredential.objects.create(
        provider=provider_a,
        name="a",
        api_key="key-a",
        priority=10,
        last_used_at=now - timedelta(hours=2),
        is_active=True,
    )
    second = ProviderCredential.objects.create(
        provider=provider_a,
        name="b",
        api_key="key-b",
        priority=10,
        last_used_at=now - timedelta(hours=1),
        is_active=True,
    )
    got1 = CredentialManager.acquire("speechmatics")
    assert got1 is not None and got1.pk == first.pk
    got2 = CredentialManager.acquire("speechmatics")
    assert got2 is not None and got2.pk == second.pk


@pytest.mark.django_db
def test_mark_failure_quota_sets_cooldown(provider_a):
    cred = ProviderCredential.objects.create(
        provider=provider_a,
        name="q",
        api_key="k",
        is_active=True,
    )
    before = timezone.now()
    CredentialManager.mark_failure(cred, "PROVIDER_QUOTA")
    cred.refresh_from_db()
    assert cred.failure_count == 1
    assert cred.last_error_code == "PROVIDER_QUOTA"
    assert cred.last_error_at is not None
    assert cred.cooldown_until is not None
    assert cred.cooldown_until > before
    assert not CredentialManager.is_available(cred)
    assert CredentialManager.acquire("speechmatics") is None


@pytest.mark.django_db
def test_mark_failure_auth_longer_cooldown_than_quota(provider_a):
    quota = ProviderCredential.objects.create(
        provider=provider_a,
        name="quota",
        api_key="kq",
        is_active=True,
    )
    auth = ProviderCredential.objects.create(
        provider=provider_a,
        name="auth",
        api_key="ka",
        is_active=True,
    )
    CredentialManager.mark_failure(quota, "PROVIDER_QUOTA")
    CredentialManager.mark_failure(auth, "PROVIDER_AUTH")
    quota.refresh_from_db()
    auth.refresh_from_db()
    assert auth.cooldown_until > quota.cooldown_until


@pytest.mark.django_db
def test_mark_failure_preserves_longer_existing_cooldown(provider_a):
    far = timezone.now() + timedelta(days=2)
    cred = ProviderCredential.objects.create(
        provider=provider_a,
        name="long",
        api_key="k",
        is_active=True,
        cooldown_until=far,
        failure_count=3,
    )
    CredentialManager.mark_failure(cred, "PROVIDER_QUOTA")
    cred.refresh_from_db()
    assert cred.failure_count == 4
    assert cred.cooldown_until == far


@pytest.mark.django_db
def test_mark_failure_transient_does_not_cooldown(provider_a):
    cred = ProviderCredential.objects.create(
        provider=provider_a,
        name="ok",
        api_key="k",
        is_active=True,
    )
    CredentialManager.mark_failure(cred, "PROVIDER_SERVER")
    cred.refresh_from_db()
    assert cred.failure_count == 0
    assert cred.cooldown_until is None
    assert CredentialManager.is_available(cred)


@pytest.mark.django_db
def test_deactivate_skips_new_attempt_but_running_sticky_still_works(provider_a, job):
    pool_secret = "active-then-off"
    attempt = _submit_job_with_pool_key(
        job, provider_a, pool_secret=pool_secret, external_id="ext-deact-1"
    )
    cred = attempt.provider_credential
    assert cred is not None
    cred.is_active = False
    cred.save(update_fields=["is_active", "updated_at"])

    assert CredentialManager.acquire("speechmatics") is None

    constructed: list[str] = []
    real_init = SpeechmaticsClient.__init__

    def tracking_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        constructed.append(self.api_key)

    with patch.object(SpeechmaticsClient, "__init__", tracking_init), patch.object(
        SpeechmaticsClient,
        "get_job",
        return_value={"job": {"id": "ext-deact-1", "status": "running"}},
    ):
        TranscriptionService().poll_once(str(job.id))
    assert constructed == [pool_secret]


@pytest.mark.django_db
def test_adapter_injected_clients_do_not_share_cache():
    from turing.providers.speechmatics.adapter import SpeechmaticsAdapter

    client_a = SpeechmaticsClient(api_key="cred-a-key")
    client_b = SpeechmaticsClient(api_key="cred-b-key")
    adapter_a = SpeechmaticsAdapter(client=client_a)
    adapter_b = SpeechmaticsAdapter(client=client_b)
    assert adapter_a._get_client() is client_a
    assert adapter_b._get_client() is client_b
    assert adapter_a._get_client().api_key == "cred-a-key"
    assert adapter_b._get_client().api_key == "cred-b-key"


@pytest.mark.django_db
def test_submit_quota_marks_credential_cooldown(provider_a, job):
    from turing.domain.exceptions import ProviderError

    ProviderCredential.objects.create(
        provider=provider_a,
        name="quota-cred",
        api_key="will-quota",
        is_active=True,
    )
    with patch.object(
        SpeechmaticsClient,
        "submit_job",
        side_effect=ProviderError(
            "rate limit",
            code="PROVIDER_QUOTA",
            retryable=True,
            provider_code="speechmatics",
        ),
    ):
        with pytest.raises(ProviderError):
            TranscriptionService().submit(str(job.id))

    cred = ProviderCredential.objects.get(name="quota-cred")
    assert cred.failure_count == 1
    assert cred.last_error_code == "PROVIDER_QUOTA"
    assert cred.cooldown_until is not None
    assert credential_signal_counts().get("credential_cooldown", 0) >= 1


# ---------------------------------------------------------------------------
# Phase 7 — operational hardening
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_acquire_result_distinguishes_empty_pool(provider_a):
    assert not ProviderCredential.objects.filter(provider=provider_a).exists()
    result = CredentialManager.acquire_result("speechmatics")
    assert result.credential is None
    assert result.outcome == AcquireOutcome.EMPTY_POOL
    assert credential_signal_counts().get("acquire_miss_empty_pool") == 1


@pytest.mark.django_db
def test_acquire_result_distinguishes_pool_exhausted(provider_a):
    ProviderCredential.objects.create(
        provider=provider_a,
        name="cooled",
        api_key="k",
        is_active=True,
        cooldown_until=timezone.now() + timedelta(hours=1),
    )
    ProviderCredential.objects.create(
        provider=provider_a,
        name="off",
        api_key="k2",
        is_active=False,
    )
    result = CredentialManager.acquire_result("speechmatics")
    assert result.credential is None
    assert result.outcome == AcquireOutcome.POOL_EXHAUSTED
    assert CredentialManager.acquire("speechmatics") is None
    assert credential_signal_counts().get("acquire_miss_pool_exhausted") >= 1


@pytest.mark.django_db
def test_begin_attempt_pool_exhausted_logs_legacy_fallback(provider_a, job):
    ProviderCredential.objects.create(
        provider=provider_a,
        name="only-cooled",
        api_key="secret-must-not-appear",
        is_active=True,
        cooldown_until=timezone.now() + timedelta(hours=2),
    )
    attempt = JobOrchestrator().begin_attempt(job)
    assert attempt.provider_credential_id is None
    counts = credential_signal_counts()
    assert counts.get("acquire_miss_pool_exhausted") == 1
    assert counts.get("legacy_fallback") == 1
    logs = list(ProcessingLog.objects.filter(job=job).order_by("created_at"))
    messages = " ".join(log.message for log in logs)
    contexts = " ".join(str(log.context) for log in logs)
    assert "exhausted" in messages.lower() or "pool exhausted" in messages.lower()
    assert "secret-must-not-appear" not in messages
    assert "secret-must-not-appear" not in contexts
    assert any(
        (log.context or {}).get("acquire_outcome") == "pool_exhausted" for log in logs
    )


@pytest.mark.django_db
def test_begin_attempt_empty_pool_signals_legacy_fallback(job):
    attempt = JobOrchestrator().begin_attempt(job)
    assert attempt.provider_credential_id is None
    counts = credential_signal_counts()
    assert counts.get("acquire_miss_empty_pool") == 1
    assert counts.get("legacy_fallback") == 1


@pytest.mark.django_db
def test_attempt_admin_credential_display_hides_secret(provider_a, job):
    from django.contrib.admin.sites import AdminSite

    from turing.admin.job import ProcessingAttemptInline

    secret = "admin-must-never-show-this-key"
    cred = ProviderCredential.objects.create(
        provider=provider_a,
        name="visible-name",
        api_key=secret,
        is_active=True,
    )
    attempt = JobOrchestrator().begin_attempt(job)
    inline = ProcessingAttemptInline(ProcessingAttempt, AdminSite())
    identity = inline.provider_credential_identity(attempt)
    assert "visible-name" in identity
    assert str(cred.pk) in identity
    assert "speechmatics" in identity
    assert secret not in identity
    assert inline.credential_id_display(attempt) == str(cred.pk)
    assert inline.credential_name_display(attempt) == "visible-name"
    assert secret not in inline.credential_name_display(attempt)


@pytest.mark.django_db
def test_fake_api_key_provider_sticky_lifecycle(db):
    """
    Provider-agnostic sticky lifecycle via injected ApiKeyClient (not Speechmatics).
    """
    from turing.domain.pipeline import PollAction
    from turing.providers.api_key_client import ApiKeyClient
    from turing.providers.registry import ProviderRegistry
    from turing.providers.types import (
        NormalizedSegment,
        NormalizedSpeaker,
        NormalizedTranscript,
        ProviderJobHandle,
        ProviderJobStatus,
    )

    FAKE_CODE = "fake-api-key-stt"
    ops: list[tuple[str, str | None]] = []

    class TrackingFakeApiKeySTT:
        code = FAKE_CODE
        display_name = "Fake API Key STT"

        def __init__(self, client=None) -> None:
            self.client = client
            self.api_key = getattr(client, "api_key", None) if client else None

        def submit(self, request):
            ops.append(("submit", self.api_key))
            return ProviderJobHandle(
                external_job_id="fake-ext-1", provider_code=FAKE_CODE
            )

        def get_status(self, handle):
            ops.append(("poll", self.api_key))
            return ProviderJobStatus(
                external_job_id=handle.external_job_id,
                state="succeeded",
                message="",
            )

        def fetch_result(self, handle):
            ops.append(("fetch", self.api_key))
            return NormalizedTranscript(
                language_code="fa",
                full_text="S1: hi",
                confidence_avg=0.9,
                speakers=[NormalizedSpeaker(label="S1", display_name="S1")],
                segments=[
                    NormalizedSegment(
                        sequence=0,
                        text="hi",
                        start_ms=0,
                        end_ms=100,
                        confidence=0.9,
                        speaker_label="S1",
                    )
                ],
            )

        def cancel(self, handle):
            ops.append(("cancel", self.api_key))
            return None

    ProviderRegistry.register(TrackingFakeApiKeySTT)
    try:
        provider = SpeechProviderConfig.objects.create(
            code=FAKE_CODE,
            name="Fake API Key STT",
            is_active=True,
        )
        cred_a = ProviderCredential.objects.create(
            provider=provider,
            name="fake-a",
            api_key="fake-secret-a",
            priority=10,
            is_active=True,
        )
        cred_b = ProviderCredential.objects.create(
            provider=provider,
            name="fake-b",
            api_key="fake-secret-b",
            priority=20,
            is_active=True,
        )
        media = MediaService().create_from_upload(
            uploaded_file=io.BytesIO(b"audio-bytes"),
            filename="fake.wav",
            use_case=UseCase.VOICE_FILE,
        )
        orch = JobOrchestrator()
        job = orch.create_transcription_job(
            media=media,
            language_code="fa",
            provider_code=FAKE_CODE,
            auto_enqueue=False,
        )
        service = TranscriptionService()

        assert service.submit(str(job.id)) == "submitted"
        job.refresh_from_db()
        attempt1 = job.attempts.get(attempt_number=1)
        assert attempt1.provider_credential_id == cred_a.pk

        outcome = service.poll_once(str(job.id))
        assert outcome.action == PollAction.READY
        orch.cancel_provider_job(
            job,
            external_job_id=job.external_job_id,
            provider_code=FAKE_CODE,
            attempt=attempt1,
        )
        service.fetch_and_persist(str(job.id))

        assert ops == [
            ("submit", "fake-secret-a"),
            ("poll", "fake-secret-a"),
            ("cancel", "fake-secret-a"),
            ("fetch", "fake-secret-a"),
        ]
        provider_obj = service._provider_for_attempt(job, attempt1)
        assert isinstance(provider_obj.client, ApiKeyClient)
        assert provider_obj.client.api_key == "fake-secret-a"

        # New job after cooling A → acquire may select B (rotation via new Attempt).
        CredentialManager.mark_failure(cred_a, "PROVIDER_QUOTA")
        media2 = MediaService().create_from_upload(
            uploaded_file=io.BytesIO(b"audio-bytes-2"),
            filename="fake2.wav",
            use_case=UseCase.VOICE_FILE,
        )
        job2 = orch.create_transcription_job(
            media=media2,
            language_code="fa",
            provider_code=FAKE_CODE,
            auto_enqueue=False,
        )
        ops.clear()
        assert service.submit(str(job2.id)) == "submitted"
        attempt2 = job2.attempts.get(attempt_number=1)
        assert attempt2.provider_credential_id == cred_b.pk
        assert ops == [("submit", "fake-secret-b")]
    finally:
        ProviderRegistry._providers.pop(FAKE_CODE, None)
