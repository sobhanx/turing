from __future__ import annotations

import io
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from turing.domain.enums import AnalysisType, TuringRole, UseCase
from turing.models import Organization, TranscriptAnalysis, TuringMembership
from turing.providers.types import (
    NormalizedSegment,
    NormalizedSpeaker,
    NormalizedTranscript,
    ProviderJobHandle,
    ProviderJobStatus,
)
from turing.services.job_orchestrator import JobOrchestrator
from turing.services.media import MediaService
from turing.services.speech_center import SpeechCenterService
from turing.services.transcript_analysis import TranscriptAnalysisService
from turing.services.transcription import TranscriptionService

User = get_user_model()
BASE = "/api/turing/v1/speech-center/"


class _FakeSTTProvider:
    code = "speechmatics"

    def submit(self, request):
        return ProviderJobHandle(external_job_id="ext-sci", provider_code=self.code)

    def get_status(self, handle):
        return ProviderJobStatus(external_job_id=handle.external_job_id, state="succeeded")

    def fetch_result(self, handle):
        return NormalizedTranscript(
            language_code="en",
            full_text="S1: Close the renewal this week.",
            confidence_avg=0.92,
            speakers=[NormalizedSpeaker(label="S1")],
            segments=[
                NormalizedSegment(
                    sequence=0,
                    text="Close the renewal this week.",
                    start_ms=0,
                    end_ms=1800,
                    confidence=0.92,
                    speaker_label="S1",
                )
            ],
        )


def _membership(user, org, role: str) -> TuringMembership:
    return TuringMembership.objects.create(
        user=user,
        organization=org,
        role=role,
        is_active=True,
    )


@pytest.fixture
def intel_setup(db, monkeypatch):
    monkeypatch.setattr(
        "turing.providers.registry.ProviderRegistry.get",
        lambda code: _FakeSTTProvider(),
    )
    org = Organization.get_default()
    viewer = User.objects.create_user(username="sci-viewer", password="pass")
    outsider = User.objects.create_user(username="sci-outsider", password="pass")
    other_org = Organization.objects.create(name="Other SCI", slug="sci-other")
    _membership(viewer, org, TuringRole.VIEWER)
    _membership(outsider, other_org, TuringRole.VIEWER)

    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="intel-call.wav",
        use_case=UseCase.CRM_CALL,
        organization=org,
    )
    job = JobOrchestrator().create_transcription_job(
        media=media,
        language_code="en",
        auto_enqueue=False,
    )
    service = TranscriptionService()
    service.submit(str(job.id))
    transcript = service.fetch_and_persist(str(job.id))
    return {
        "org": org,
        "viewer": viewer,
        "outsider": outsider,
        "transcript": transcript,
    }


@pytest.fixture
def viewer_client(intel_setup):
    client = APIClient()
    client.force_authenticate(user=intel_setup["viewer"])
    return client


def _write_analysis(transcript, *, analysis_type, content, created_at=None, provider="fake"):
    row = TranscriptAnalysis.objects.create(
        transcript=transcript,
        organization=transcript.organization,
        analysis_type=analysis_type,
        content=content,
        provider=provider,
        model_name="test-model",
    )
    if created_at is not None:
        TranscriptAnalysis.objects.filter(pk=row.pk).update(created_at=created_at)
        row.refresh_from_db()
    return row


@pytest.mark.django_db
def test_latest_analysis_selection(intel_setup):
    transcript = intel_setup["transcript"]
    older = timezone.now() - timedelta(hours=2)
    newer = timezone.now() - timedelta(minutes=5)
    _write_analysis(
        transcript,
        analysis_type=AnalysisType.SUMMARY,
        content={"summary": "old", "main_points": []},
        created_at=older,
    )
    _write_analysis(
        transcript,
        analysis_type=AnalysisType.SUMMARY,
        content={"summary": "new latest", "main_points": ["a"]},
        created_at=newer,
    )
    _write_analysis(
        transcript,
        analysis_type=AnalysisType.TOPICS,
        content=["renewal"],
        created_at=newer,
    )
    _write_analysis(
        transcript,
        analysis_type=AnalysisType.ACTION_ITEMS,
        content=[{"task": "Send quote"}],
        created_at=newer,
    )

    payload = SpeechCenterService().get_latest_intelligence(transcript)
    assert payload["summary"]["summary"] == "new latest"
    assert payload["topics"] == ["renewal"]
    assert payload["action_items"][0]["task"] == "Send quote"
    assert payload["metadata"]["summary_id"] is not None
    assert AnalysisType.SUMMARY in payload["metadata"]["available_types"]
    assert payload["generated_at"] is not None

    # aggregate_analyses from history list also keeps latest
    history = list(
        TranscriptAnalysis.objects.filter(transcript=transcript).order_by("created_at")
    )
    aggregated = SpeechCenterService().aggregate_analyses(history)
    assert aggregated["summary"]["summary"] == "new latest"

    # intelligence_summary alias
    assert (
        SpeechCenterService().intelligence_summary(transcript)["summary"]["summary"]
        == "new latest"
    )


@pytest.mark.django_db
def test_missing_analysis_behavior(intel_setup):
    transcript = intel_setup["transcript"]
    _write_analysis(
        transcript,
        analysis_type=AnalysisType.SUMMARY,
        content={"summary": "only summary", "main_points": []},
    )
    payload = SpeechCenterService().get_latest_intelligence(transcript)
    assert payload["summary"]["summary"] == "only summary"
    assert payload["topics"] is None
    assert payload["action_items"] is None
    assert payload["metadata"]["topics_id"] is None
    assert payload["metadata"]["available_types"] == [AnalysisType.SUMMARY]


@pytest.mark.django_db
def test_intelligence_endpoint(viewer_client, intel_setup):
    transcript = intel_setup["transcript"]
    TranscriptAnalysisService().generate_default_suite(transcript, provider_code="fake")
    response = viewer_client.get(f"{BASE}{transcript.id}/intelligence/")
    assert response.status_code == 200
    data = response.data
    assert data["transcript_id"] == str(transcript.id)
    assert set(data["intelligence"].keys()) == {
        "summary",
        "topics",
        "action_items",
    }
    assert data["intelligence"]["summary"] is not None
    assert data["intelligence"]["topics"] is not None
    assert data["intelligence"]["action_items"] is not None
    assert data["generated_at"] is not None


@pytest.mark.django_db
def test_intelligence_endpoint_empty(viewer_client, intel_setup):
    transcript = intel_setup["transcript"]
    response = viewer_client.get(f"{BASE}{transcript.id}/intelligence/")
    assert response.status_code == 200
    assert response.data["intelligence"]["summary"] is None
    assert response.data["intelligence"]["topics"] is None
    assert response.data["intelligence"]["action_items"] is None
    assert response.data["generated_at"] is None


@pytest.mark.django_db
def test_org_isolation(intel_setup):
    transcript = intel_setup["transcript"]
    TranscriptAnalysisService().generate_default_suite(transcript, provider_code="fake")
    outsider = APIClient()
    outsider.force_authenticate(user=intel_setup["outsider"])
    response = outsider.get(f"{BASE}{transcript.id}/intelligence/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_permissions_require_auth(intel_setup):
    anon = APIClient()
    response = anon.get(f"{BASE}{intel_setup['transcript'].id}/intelligence/")
    assert response.status_code in {401, 403}


@pytest.mark.django_db
def test_existing_endpoints_still_work(viewer_client, intel_setup):
    transcript = intel_setup["transcript"]
    TranscriptAnalysisService().generate_default_suite(transcript, provider_code="fake")
    timeline = viewer_client.get(f"{BASE}{transcript.id}/timeline/")
    assert timeline.status_code == 200
    assert "segments" in timeline.data
