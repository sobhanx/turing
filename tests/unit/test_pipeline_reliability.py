"""Phase 2.8 — Pipeline reliability / race-condition tests."""

from __future__ import annotations

import io

import pytest
from django.contrib.auth import get_user_model

from turing.domain.enums import JobStatus, UseCase
from turing.domain.exceptions import JobStateError, TuringError
from turing.domain.policies import (
    assert_job_can_succeed,
    assert_job_transition,
)
from turing.models import ProcessingJob, Transcript
from turing.providers.types import (
    NormalizedSegment,
    NormalizedSpeaker,
    NormalizedTranscript,
    ProviderJobHandle,
    ProviderJobStatus,
)
from turing.services.job_orchestrator import JobOrchestrator
from turing.services.media import MediaService
from turing.services.transcription import PIPELINE_META_KEY, TranscriptionService

User = get_user_model()


class TrackingProvider:
    code = "speechmatics"

    def __init__(self) -> None:
        self.submit_calls = 0
        self.cancel_calls: list[str] = []
        self.submit_ids = ["ext-a", "ext-b"]
        self.fetch_hook = None

    def submit(self, request):
        self.submit_calls += 1
        idx = min(self.submit_calls - 1, len(self.submit_ids) - 1)
        return ProviderJobHandle(
            external_job_id=self.submit_ids[idx],
            provider_code=self.code,
        )

    def get_status(self, handle):
        return ProviderJobStatus(
            external_job_id=handle.external_job_id,
            state="succeeded",
        )

    def fetch_result(self, handle):
        if self.fetch_hook:
            return self.fetch_hook(handle)
        return NormalizedTranscript(
            language_code="en",
            full_text="Hello",
            confidence_avg=0.9,
            speakers=[NormalizedSpeaker(label="S1")],
            segments=[
                NormalizedSegment(
                    sequence=0,
                    text="Hello",
                    start_ms=0,
                    end_ms=100,
                    confidence=0.9,
                    speaker_label="S1",
                )
            ],
        )

    def cancel(self, handle):
        self.cancel_calls.append(handle.external_job_id)


@pytest.fixture
def media(db):
    return MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio-bytes"),
        filename="clip.wav",
        use_case=UseCase.VOICE_FILE,
    )


@pytest.fixture
def job(media):
    return JobOrchestrator().create_transcription_job(
        media=media,
        language_code="en",
        auto_enqueue=False,
    )


def _patch_provider(monkeypatch, provider):
    monkeypatch.setattr(
        "turing.providers.registry.ProviderRegistry.get",
        lambda code: provider,
    )


@pytest.mark.django_db
def test_submit_claim_prevents_duplicate_provider_calls(monkeypatch, job):
    provider = TrackingProvider()
    _patch_provider(monkeypatch, provider)
    service = TranscriptionService()

    assert service.submit(str(job.id)) == "submitted"
    assert provider.submit_calls == 1

    # Active claim / external id → second submit does not call provider again
    assert service.submit(str(job.id)) == "already_submitted"
    assert provider.submit_calls == 1


@pytest.mark.django_db
def test_concurrent_submit_cancels_orphan_provider_job(monkeypatch, job):
    provider = TrackingProvider()
    _patch_provider(monkeypatch, provider)
    service = TranscriptionService()

    assert service.submit(str(job.id)) == "submitted"
    job.refresh_from_db()
    assert job.external_job_id == "ext-a"

    # Prepare a second submit attempt: clear external id + claim stage
    job.external_job_id = ""
    job.save(update_fields=["external_job_id", "updated_at"])
    attempt = job.attempts.order_by("-attempt_number").first()
    meta = dict(attempt.response_metadata or {})
    meta[PIPELINE_META_KEY] = {"stage": "submit_failed"}
    attempt.response_metadata = meta
    attempt.save(update_fields=["response_metadata", "updated_at"])

    original_submit = provider.submit

    def racing_submit(request):
        handle = original_submit(request)
        # Winner already committed while we were in provider I/O
        ProcessingJob.objects.filter(pk=job.id).update(external_job_id="ext-a")
        return handle

    provider.submit = racing_submit  # type: ignore[method-assign]
    result = service.submit(str(job.id))
    assert result == "already_submitted"
    assert "ext-b" in provider.cancel_calls
    job.refresh_from_db()
    assert job.external_job_id == "ext-a"


@pytest.mark.django_db
def test_cancel_is_provider_aware(monkeypatch, job):
    provider = TrackingProvider()
    _patch_provider(monkeypatch, provider)
    service = TranscriptionService()
    service.submit(str(job.id))

    JobOrchestrator().cancel(job)
    job.refresh_from_db()
    assert job.status == JobStatus.CANCELLED
    assert provider.cancel_calls == ["ext-a"]


@pytest.mark.django_db
def test_cancel_during_fetch_does_not_mark_succeeded(monkeypatch, job):
    provider = TrackingProvider()
    _patch_provider(monkeypatch, provider)
    service = TranscriptionService()
    service.submit(str(job.id))

    def fetch_and_cancel(handle):
        JobOrchestrator().cancel(ProcessingJob.objects.get(pk=job.id))
        return NormalizedTranscript(
            language_code="en",
            full_text="X",
            confidence_avg=0.5,
            speakers=[],
            segments=[],
        )

    provider.fetch_hook = fetch_and_cancel

    with pytest.raises(TuringError, match="cancelled"):
        service.fetch_and_persist(str(job.id))

    job.refresh_from_db()
    assert job.status == JobStatus.CANCELLED
    assert not Transcript.objects.filter(job=job).exists()


@pytest.mark.django_db
def test_mark_succeeded_skipped_when_cancelled(monkeypatch, job):
    provider = TrackingProvider()
    _patch_provider(monkeypatch, provider)
    service = TranscriptionService()
    service.submit(str(job.id))
    job.refresh_from_db()
    attempt = job.attempts.order_by("-attempt_number").first()

    JobOrchestrator().cancel(job)
    ok = JobOrchestrator().mark_succeeded(job, attempt)
    assert ok is False
    job.refresh_from_db()
    assert job.status == JobStatus.CANCELLED


@pytest.mark.django_db
def test_concurrent_persist_returns_same_transcript(monkeypatch, job):
    provider = TrackingProvider()
    _patch_provider(monkeypatch, provider)
    service = TranscriptionService()
    service.submit(str(job.id))

    t1 = service.fetch_and_persist(str(job.id))
    t2 = service.fetch_and_persist(str(job.id))
    assert t1.id == t2.id
    assert Transcript.objects.filter(job=job).count() == 1
    job.refresh_from_db()
    assert job.status == JobStatus.SUCCEEDED


@pytest.mark.django_db
def test_persist_integrity_error_returns_existing(monkeypatch, job):
    """IntegrityError on create resolves to the winning transcript row."""
    provider = TrackingProvider()
    _patch_provider(monkeypatch, provider)
    TranscriptionService().submit(str(job.id))
    job.refresh_from_db()

    from django.db import IntegrityError
    from turing.domain.enums import TranscriptStatus
    from turing.services.transcript import TranscriptService as TS

    winner = Transcript.objects.create(
        job=job,
        media=job.media,
        organization=job.organization,
        language_code="en",
        status=TranscriptStatus.DRAFT,
        full_text="winner",
        is_primary=True,
    )

    state = {"n": 0}
    original_filter = Transcript.objects.filter

    def filter_race(*args, **kwargs):
        if kwargs.get("job") is job:
            state["n"] += 1
            if state["n"] == 1:
                class _Empty:
                    def first(self):
                        return None

                return _Empty()
        return original_filter(*args, **kwargs)

    monkeypatch.setattr(Transcript.objects, "filter", filter_race)
    monkeypatch.setattr(
        Transcript.objects,
        "create",
        lambda *a, **k: (_ for _ in ()).throw(IntegrityError("dup")),
    )

    result = TS().persist_from_provider(
        job=job,
        normalized=NormalizedTranscript(
            language_code="en",
            full_text="loser",
            confidence_avg=0.1,
            speakers=[],
            segments=[],
        ),
    )
    assert result.id == winner.id


@pytest.mark.django_db
def test_lifecycle_transition_validation():
    assert_job_transition(JobStatus.PENDING, JobStatus.QUEUED)
    assert_job_transition(JobStatus.RUNNING, JobStatus.SUCCEEDED)
    with pytest.raises(JobStateError):
        assert_job_transition(JobStatus.SUCCEEDED, JobStatus.RUNNING)
    with pytest.raises(JobStateError):
        assert_job_can_succeed(JobStatus.CANCELLED)


@pytest.mark.django_db
def test_enqueue_rejects_running_job(job, monkeypatch):
    provider = TrackingProvider()
    _patch_provider(monkeypatch, provider)
    TranscriptionService().submit(str(job.id))
    job.refresh_from_db()
    assert job.status == JobStatus.RUNNING
    with pytest.raises(JobStateError):
        JobOrchestrator().enqueue(job)


@pytest.mark.django_db
def test_empty_external_job_id_fails_submit(monkeypatch, job):
    provider = TrackingProvider()

    def empty_submit(request):
        provider.submit_calls += 1
        return ProviderJobHandle(external_job_id="", provider_code=provider.code)

    provider.submit = empty_submit  # type: ignore[method-assign]
    _patch_provider(monkeypatch, provider)
    with pytest.raises(Exception):
        TranscriptionService().submit(str(job.id))
    job.refresh_from_db()
    assert job.status == JobStatus.FAILED
    assert job.error_code == "PROVIDER_RESPONSE"
