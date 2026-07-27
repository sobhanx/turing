from __future__ import annotations

import io
from unittest.mock import MagicMock

import pytest
from django.contrib.auth import get_user_model

from turing.ai.interfaces import AIProvider
from turing.domain.enums import AnalysisType, UseCase
from turing.domain.exceptions import PermissionDeniedError, ProviderError
from turing.models import Organization, Transcript, TranscriptAnalysis, TranscriptRevision
from turing.providers.types import (
    NormalizedSegment,
    NormalizedSpeaker,
    NormalizedTranscript,
    ProviderJobHandle,
    ProviderJobStatus,
)
from turing.services.job_orchestrator import JobOrchestrator
from turing.services.media import MediaService
from turing.services.transcript_analysis import TranscriptAnalysisService
from turing.services.transcription import TranscriptionService
from turing.tasks import analysis as analysis_tasks
from turing.tasks import transcription as transcription_tasks


User = get_user_model()


class FakeSTTProvider:
    code = "speechmatics"

    def submit(self, request):
        return ProviderJobHandle(external_job_id="ext-analysis-1", provider_code=self.code)

    def get_status(self, handle):
        return ProviderJobStatus(external_job_id=handle.external_job_id, state="succeeded")

    def fetch_result(self, handle):
        return NormalizedTranscript(
            language_code="en",
            full_text="S1: Discuss pricing and contract timeline.",
            confidence_avg=0.9,
            speakers=[NormalizedSpeaker(label="S1")],
            segments=[
                NormalizedSegment(
                    sequence=0,
                    text="Discuss pricing and contract timeline.",
                    start_ms=0,
                    end_ms=2000,
                    confidence=0.9,
                    speaker_label="S1",
                )
            ],
        )


@pytest.fixture
def transcript(db, monkeypatch):
    monkeypatch.setattr(
        "turing.providers.registry.ProviderRegistry.get",
        lambda code: FakeSTTProvider(),
    )
    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="meeting.wav",
        use_case=UseCase.MEETING,
    )
    job = JobOrchestrator().create_transcription_job(
        media=media,
        language_code="en",
        auto_enqueue=False,
    )
    service = TranscriptionService()
    service.submit(str(job.id))
    transcript = service.fetch_and_persist(str(job.id))
    assert transcript.full_text
    return transcript


@pytest.mark.django_db
def test_analysis_does_not_mutate_transcript(transcript):
    before = {
        "full_text": transcript.full_text,
        "version": transcript.version,
        "metadata": dict(transcript.metadata),
        "revision_count": TranscriptRevision.objects.filter(transcript=transcript).count(),
        "segment_count": transcript.segments.count(),
    }

    TranscriptAnalysisService().generate_default_suite(transcript, provider_code="fake")

    transcript.refresh_from_db()
    assert transcript.full_text == before["full_text"]
    assert transcript.version == before["version"]
    assert transcript.metadata == before["metadata"]
    assert (
        TranscriptRevision.objects.filter(transcript=transcript).count()
        == before["revision_count"]
    )
    assert transcript.segments.count() == before["segment_count"]


@pytest.mark.django_db
def test_analysis_linked_with_organization(transcript):
    analyses = TranscriptAnalysisService().generate_default_suite(
        transcript,
        provider_code="fake",
    )
    assert len(analyses) == 3
    for analysis in analyses:
        assert analysis.transcript_id == transcript.id
        assert analysis.organization_id == transcript.organization_id
        assert analysis.provider == "fake"
        assert analysis.model_name


@pytest.mark.django_db
def test_multiple_analyses_allowed_per_transcript(transcript):
    service = TranscriptAnalysisService()
    first = service.generate_default_suite(transcript, provider_code="fake")
    second = service.generate(
        transcript,
        analysis_types=[AnalysisType.SUMMARY],
        provider_code="fake",
    )
    assert len(first) == 3
    assert len(second) == 1
    assert TranscriptAnalysis.objects.filter(transcript=transcript).count() == 4
    assert (
        TranscriptAnalysis.objects.filter(
            transcript=transcript,
            analysis_type=AnalysisType.SUMMARY,
        ).count()
        == 2
    )


@pytest.mark.django_db
def test_fake_provider_outputs_expected_shapes(transcript):
    analyses = {
        item.analysis_type: item
        for item in TranscriptAnalysisService().generate_default_suite(
            transcript,
            provider_code="fake",
        )
    }
    summary = analyses[AnalysisType.SUMMARY].content
    assert isinstance(summary, dict)
    assert isinstance(summary["summary"], str)
    assert isinstance(summary["main_points"], list)

    action_items = analyses[AnalysisType.ACTION_ITEMS].content
    assert isinstance(action_items, list)
    assert action_items[0]["task"]
    assert action_items[0]["owner"] is None

    topics = analyses[AnalysisType.TOPICS].content
    assert isinstance(topics, list)
    assert all(isinstance(topic, str) for topic in topics)


@pytest.mark.django_db
def test_failed_provider_does_not_corrupt_transcript(transcript, monkeypatch):
    class FailingProvider(AIProvider):
        code = "failing"

        def summarize(self, transcript_input):
            raise ProviderError("boom", code="PROVIDER_RESPONSE", retryable=False)

        def extract_action_items(self, transcript_input):
            raise ProviderError("boom", code="PROVIDER_RESPONSE", retryable=False)

        def extract_topics(self, transcript_input):
            raise ProviderError("boom", code="PROVIDER_RESPONSE", retryable=False)

    monkeypatch.setattr(
        "turing.services.transcript_analysis.AIProviderRegistry.get",
        lambda code: FailingProvider(),
    )
    before_text = transcript.full_text

    with pytest.raises(ProviderError):
        TranscriptAnalysisService().generate_default_suite(transcript, provider_code="failing")

    transcript.refresh_from_db()
    assert transcript.full_text == before_text
    assert TranscriptAnalysis.objects.filter(transcript=transcript).count() == 0


@pytest.mark.django_db
def test_fetch_task_schedules_analysis_only_on_create(monkeypatch, db):
    provider = FakeSTTProvider()
    monkeypatch.setattr(
        "turing.providers.registry.ProviderRegistry.get",
        lambda code: provider,
    )
    scheduled: list[str] = []
    monkeypatch.setattr(
        "turing.tasks.analysis.generate_transcript_analysis.delay",
        lambda transcript_id: scheduled.append(transcript_id) or MagicMock(),
    )

    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="meeting.wav",
        use_case=UseCase.MEETING,
    )
    job = JobOrchestrator().create_transcription_job(
        media=media,
        language_code="en",
        auto_enqueue=False,
    )
    TranscriptionService().submit(str(job.id))

    transcription_tasks.fetch_and_persist_transcription.run(str(job.id))
    assert len(scheduled) == 1

    transcription_tasks.fetch_and_persist_transcription.run(str(job.id))
    assert len(scheduled) == 1


@pytest.mark.django_db
def test_generate_transcript_analysis_task_uses_fake_provider(transcript, monkeypatch):
    monkeypatch.setattr(
        "turing.services.transcript_analysis.AIProviderRegistry.get",
        lambda code: __import__(
            "turing.ai.providers.fake",
            fromlist=["FakeAIProvider"],
        ).FakeAIProvider(),
    )
    result = analysis_tasks.generate_transcript_analysis.run(str(transcript.id))
    assert result == "created:3"
    assert TranscriptAnalysis.objects.filter(transcript=transcript).count() == 3


@pytest.mark.django_db
def test_tenant_isolation_on_analysis_queryset(db, transcript):
    other_org = Organization.objects.create(name="Other", slug="other-ai", is_active=True)
    other_media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="other.wav",
        use_case=UseCase.MEETING,
        organization=other_org,
    )
    other_job = JobOrchestrator().create_transcription_job(
        media=other_media,
        language_code="en",
        auto_enqueue=False,
    )
    other_transcript = Transcript.objects.create(
        job=other_job,
        media=other_media,
        organization=other_org,
        full_text="secret",
    )
    TranscriptAnalysisService().generate_default_suite(transcript, provider_code="fake")
    TranscriptAnalysisService().generate_default_suite(other_transcript, provider_code="fake")

    user = User.objects.create_user(username="viewer-ai", password="pass")
    from turing.domain.enums import TuringRole
    from turing.models import TuringMembership

    TuringMembership.objects.create(
        user=user,
        organization=transcript.organization,
        role=TuringRole.VIEWER,
        is_active=True,
    )

    service = TranscriptAnalysisService()
    visible = service.scope_queryset(TranscriptAnalysis.objects.all(), user)
    assert visible.filter(transcript=transcript).exists()
    assert not visible.filter(transcript=other_transcript).exists()

    with pytest.raises(PermissionDeniedError):
        service.generate(
            other_transcript,
            analysis_types=[AnalysisType.SUMMARY],
            user=user,
            provider_code="fake",
        )


@pytest.mark.django_db
def test_fetch_and_persist_public_return_unchanged(monkeypatch, db):
    provider = FakeSTTProvider()
    monkeypatch.setattr(
        "turing.providers.registry.ProviderRegistry.get",
        lambda code: provider,
    )
    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="meeting.wav",
        use_case=UseCase.MEETING,
    )
    job = JobOrchestrator().create_transcription_job(
        media=media,
        language_code="en",
        auto_enqueue=False,
    )
    service = TranscriptionService()
    service.submit(str(job.id))
    result = service.fetch_and_persist(str(job.id))
    assert isinstance(result, Transcript)
    transcript, created = service._fetch_and_persist_with_created(str(job.id))
    assert transcript.id == result.id
    assert created is False
