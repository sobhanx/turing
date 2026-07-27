from __future__ import annotations

import io
from unittest.mock import MagicMock

import pytest
from django.contrib.auth import get_user_model

from turing.domain.enums import JobStatus, UseCase
from turing.domain.exceptions import ProviderError
from turing.domain.pipeline import PollAction, compute_poll_countdown
from turing.models import Transcript
from turing.providers.types import (
    NormalizedSegment,
    NormalizedSpeaker,
    NormalizedTranscript,
    ProviderJobHandle,
    ProviderJobStatus,
)
from turing.services.job_orchestrator import JobOrchestrator
from turing.services.media import MediaService
from turing.services.transcription import TranscriptionService
from turing.tasks import transcription as transcription_tasks


User = get_user_model()


class FakeProvider:
    code = "speechmatics"
    submit_calls = 0
    status_calls = 0
    fetch_calls = 0
    statuses: list[str]

    def __init__(self, statuses: list[str] | None = None) -> None:
        self.statuses = list(statuses or ["succeeded"])
        self.submit_calls = 0
        self.status_calls = 0
        self.fetch_calls = 0

    def submit(self, request):
        self.submit_calls += 1
        return ProviderJobHandle(external_job_id="ext-async-1", provider_code=self.code)

    def get_status(self, handle):
        self.status_calls += 1
        idx = min(self.status_calls - 1, len(self.statuses) - 1)
        state = self.statuses[idx]
        return ProviderJobStatus(
            external_job_id=handle.external_job_id,
            state=state,
            message="rejected" if state == "failed" else "",
        )

    def fetch_result(self, handle):
        self.fetch_calls += 1
        return NormalizedTranscript(
            language_code="fa",
            full_text="S1: سلام",
            confidence_avg=0.9,
            speakers=[NormalizedSpeaker(label="S1", display_name="S1")],
            segments=[
                NormalizedSegment(
                    sequence=0,
                    text="سلام",
                    start_ms=0,
                    end_ms=500,
                    confidence=0.9,
                    speaker_label="S1",
                )
            ],
        )

    def cancel(self, handle):
        return None


@pytest.fixture
def media(db):
    return MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio-bytes"),
        filename="fa.wav",
        use_case=UseCase.VOICE_FILE,
    )


@pytest.fixture
def job(media):
    return JobOrchestrator().create_transcription_job(
        media=media,
        language_code="fa",
        auto_enqueue=False,
    )


def test_compute_poll_countdown_grows_and_caps():
    assert compute_poll_countdown(0, base_seconds=3, max_seconds=60, jitter_ratio=0) == 3
    assert compute_poll_countdown(1, base_seconds=3, max_seconds=60, jitter_ratio=0) == 6
    assert compute_poll_countdown(10, base_seconds=3, max_seconds=60, jitter_ratio=0) == 60


@pytest.mark.django_db
def test_submit_is_idempotent(monkeypatch, job):
    provider = FakeProvider()
    monkeypatch.setattr(
        "turing.providers.registry.ProviderRegistry.get",
        lambda code: provider,
    )
    service = TranscriptionService()
    assert service.submit(str(job.id)) == "submitted"
    assert service.submit(str(job.id)) == "already_submitted"
    assert provider.submit_calls == 1
    job.refresh_from_db()
    assert job.external_job_id == "ext-async-1"
    assert job.status == JobStatus.RUNNING


@pytest.mark.django_db
def test_poll_reschedules_until_ready(monkeypatch, job):
    provider = FakeProvider(statuses=["running", "running", "succeeded"])
    monkeypatch.setattr(
        "turing.providers.registry.ProviderRegistry.get",
        lambda code: provider,
    )
    service = TranscriptionService()
    service.submit(str(job.id))

    first = service.poll_once(str(job.id), poll_count=0)
    assert first.action == PollAction.RESCHEDULE
    assert first.countdown > 0

    second = service.poll_once(str(job.id), poll_count=1)
    assert second.action == PollAction.RESCHEDULE

    third = service.poll_once(str(job.id), poll_count=2)
    assert third.action == PollAction.READY


@pytest.mark.django_db
def test_fetch_and_persist_duplicate_safe(monkeypatch, job):
    provider = FakeProvider()
    monkeypatch.setattr(
        "turing.providers.registry.ProviderRegistry.get",
        lambda code: provider,
    )
    service = TranscriptionService()
    service.submit(str(job.id))
    t1 = service.fetch_and_persist(str(job.id))
    t2 = service.fetch_and_persist(str(job.id))
    assert t1.id == t2.id
    assert Transcript.objects.filter(job=job).count() == 1
    assert provider.fetch_calls == 1
    job.refresh_from_db()
    assert job.status == JobStatus.SUCCEEDED


@pytest.mark.django_db
def test_provider_failure_marks_job_failed(monkeypatch, job):
    provider = FakeProvider(statuses=["failed"])
    monkeypatch.setattr(
        "turing.providers.registry.ProviderRegistry.get",
        lambda code: provider,
    )
    service = TranscriptionService()
    service.submit(str(job.id))
    outcome = service.poll_once(str(job.id), poll_count=0)
    assert outcome.action == PollAction.FAILED
    assert outcome.error_code == "PROVIDER_JOB_FAILED"
    job.refresh_from_db()
    assert job.status == JobStatus.FAILED
    assert job.error_code == "PROVIDER_JOB_FAILED"
    assert job.error_message


@pytest.mark.django_db
def test_async_task_chain_end_to_end(monkeypatch, job):
    provider = FakeProvider(statuses=["succeeded"])
    monkeypatch.setattr(
        "turing.providers.registry.ProviderRegistry.get",
        lambda code: provider,
    )

    scheduled: list[tuple] = []

    def immediate_schedule(job_id, *, poll_count, countdown):
        scheduled.append((job_id, poll_count, countdown))
        return transcription_tasks.poll_transcription_job(job_id, poll_count=poll_count)

    monkeypatch.setattr(transcription_tasks, "_schedule_poll", immediate_schedule)

    fetch_jobs: list[str] = []

    def immediate_fetch(job_id):
        fetch_jobs.append(job_id)
        return transcription_tasks.fetch_and_persist_transcription(job_id)

    monkeypatch.setattr(
        transcription_tasks.fetch_and_persist_transcription,
        "delay",
        immediate_fetch,
    )

    result = transcription_tasks.submit_transcription_job(str(job.id))
    assert result == "submitted"
    job.refresh_from_db()
    assert job.status == JobStatus.SUCCEEDED
    assert Transcript.objects.filter(job=job).exists()
    assert provider.submit_calls == 1
    assert provider.fetch_calls == 1
    assert scheduled
    assert fetch_jobs == [str(job.id)]


@pytest.mark.django_db
def test_enqueue_schedules_submit_task(monkeypatch, job):
    called = {}

    def fake_apply_async(*args, **kwargs):
        called["args"] = kwargs.get("args") or (args[0] if args else None)
        called["countdown"] = kwargs.get("countdown", 0)
        return MagicMock(id="task-1")

    monkeypatch.setattr(
        "turing.tasks.ingestion.prepare_media_for_transcription.apply_async",
        fake_apply_async,
    )
    JobOrchestrator().enqueue(job)
    job.refresh_from_db()
    assert job.status == JobStatus.QUEUED
    assert called["args"] == [str(job.id)]


@pytest.mark.django_db
def test_submit_provider_error_is_retryable_path(monkeypatch, job):
    class BoomProvider(FakeProvider):
        def submit(self, request):
            raise ProviderError("network down", code="PROVIDER_NETWORK", retryable=True)

    monkeypatch.setattr(
        "turing.providers.registry.ProviderRegistry.get",
        lambda code: BoomProvider(),
    )
    service = TranscriptionService()
    with pytest.raises(ProviderError):
        service.submit(str(job.id))
    job.refresh_from_db()
    assert job.status == JobStatus.FAILED
    assert job.error_code == "PROVIDER_NETWORK"
    assert service.should_automatic_retry(job, error_code="PROVIDER_NETWORK")
