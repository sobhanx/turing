from __future__ import annotations

"""Speech Center optional AI insights trigger tests."""

import io
from unittest.mock import MagicMock

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from turing.domain.enums import UseCase
from turing.models import Organization, TranscriptAnalysis
from turing.providers.types import (
    NormalizedSegment,
    NormalizedSpeaker,
    NormalizedTranscript,
    ProviderJobHandle,
    ProviderJobStatus,
)
from turing.services import ai_analysis_trigger as trigger
from turing.services.job_orchestrator import JobOrchestrator
from turing.services.media import MediaService
from turing.services.transcription import TranscriptionService
from turing.tasks import transcription as transcription_tasks

User = get_user_model()


class _FakeSTT:
    code = "speechmatics"

    def submit(self, request):
        return ProviderJobHandle(external_job_id="ext-ai", provider_code=self.code)

    def get_status(self, handle):
        return ProviderJobStatus(
            external_job_id=handle.external_job_id,
            state="succeeded",
        )

    def fetch_result(self, handle):
        return NormalizedTranscript(
            language_code="en",
            full_text="Hello world",
            speakers=[NormalizedSpeaker(label="S1")],
            segments=[
                NormalizedSegment(
                    sequence=0,
                    text="Hello world",
                    start_ms=0,
                    end_ms=1000,
                    speaker_label="S1",
                )
            ],
        )


@pytest.fixture
def sc_user(db):
    return User.objects.create_superuser("ai-admin", "ai@example.com", "pass")


@pytest.fixture
def sc_client(client, sc_user):
    client.force_login(sc_user)
    return client


@pytest.mark.django_db
def test_organization_auto_generate_defaults_false():
    org = Organization.get_default()
    assert org.auto_generate_ai_analysis is False


@pytest.mark.django_db
def test_transcript_completes_without_analysis_rows(monkeypatch, db):
    monkeypatch.setattr(
        "turing.providers.registry.ProviderRegistry.get",
        lambda code: _FakeSTT(),
    )
    scheduled: list[str] = []
    monkeypatch.setattr(
        "turing.tasks.analysis.generate_transcript_analysis.delay",
        lambda transcript_id: scheduled.append(transcript_id) or MagicMock(),
    )

    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="done.wav",
        use_case=UseCase.GENERIC,
    )
    Organization.objects.filter(pk=media.organization_id).update(
        auto_generate_ai_analysis=False
    )
    job = JobOrchestrator().create_transcription_job(
        media=media,
        language_code="en",
        auto_enqueue=False,
    )
    TranscriptionService().submit(str(job.id))
    transcription_tasks.fetch_and_persist_transcription.run(str(job.id))

    from turing.models import Transcript

    transcript = Transcript.objects.get(job=job)
    assert transcript.full_text
    assert TranscriptAnalysis.objects.filter(transcript=transcript).count() == 0
    assert scheduled == []


@pytest.mark.django_db
def test_auto_generate_true_preserves_previous_enqueue_behaviour(monkeypatch, db):
    monkeypatch.setattr(
        "turing.providers.registry.ProviderRegistry.get",
        lambda code: _FakeSTT(),
    )
    scheduled: list[str] = []
    monkeypatch.setattr(
        "turing.tasks.analysis.generate_transcript_analysis.delay",
        lambda transcript_id: scheduled.append(transcript_id) or MagicMock(),
    )

    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="auto.wav",
        use_case=UseCase.GENERIC,
    )
    Organization.objects.filter(pk=media.organization_id).update(
        auto_generate_ai_analysis=True
    )
    job = JobOrchestrator().create_transcription_job(
        media=media,
        language_code="en",
        auto_enqueue=False,
    )
    TranscriptionService().submit(str(job.id))
    transcription_tasks.fetch_and_persist_transcription.run(str(job.id))

    assert len(scheduled) == 1
    assert trigger.get_trigger_state(scheduled[0]) == trigger.STATE_GENERATING


@pytest.mark.django_db
def test_analysis_task_marks_failed_on_provider_error(monkeypatch, db, sc_user):
    from turing.domain.enums import TranscriptStatus
    from turing.domain.exceptions import ProviderError
    from turing.models import Transcript
    from turing.tasks import analysis as analysis_tasks

    org = Organization.get_default()
    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="fail.wav",
        use_case=UseCase.GENERIC,
        organization=org,
        uploaded_by=sc_user,
    )
    job = JobOrchestrator().create_transcription_job(
        media=media,
        created_by=sc_user,
        language_code="en",
        auto_enqueue=False,
    )
    transcript = Transcript.objects.create(
        job=job,
        media=media,
        organization=org,
        status=TranscriptStatus.DRAFT,
        language_code="en",
        full_text="text",
        is_primary=True,
        version=1,
    )

    class Boom:
        def generate_default_suite(self, *args, **kwargs):
            raise ProviderError("boom", code="PROVIDER_SERVER", retryable=False)

    monkeypatch.setattr(
        "turing.services.transcript_analysis.TranscriptAnalysisService",
        lambda: Boom(),
    )
    with pytest.raises(ProviderError):
        analysis_tasks.generate_transcript_analysis.run(str(transcript.id))
    assert trigger.get_trigger_state(str(transcript.id)) == trigger.STATE_FAILED
