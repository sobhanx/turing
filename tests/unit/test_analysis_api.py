from __future__ import annotations

import io

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from turing.domain.enums import AnalysisType, TuringRole, UseCase
from turing.models import Organization, TuringMembership
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

User = get_user_model()


class _FakeSTTProvider:
    code = "speechmatics"

    def submit(self, request):
        return ProviderJobHandle(external_job_id="ext-api-analysis", provider_code=self.code)

    def get_status(self, handle):
        return ProviderJobStatus(external_job_id=handle.external_job_id, state="succeeded")

    def fetch_result(self, handle):
        return NormalizedTranscript(
            language_code="en",
            full_text="S1: Discuss pricing and next steps.",
            confidence_avg=0.9,
            speakers=[NormalizedSpeaker(label="S1")],
            segments=[
                NormalizedSegment(
                    sequence=0,
                    text="Discuss pricing and next steps.",
                    start_ms=0,
                    end_ms=2000,
                    confidence=0.9,
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
def analysis_setup(db, monkeypatch):
    monkeypatch.setattr(
        "turing.providers.registry.ProviderRegistry.get",
        lambda code: _FakeSTTProvider(),
    )
    org = Organization.get_default()
    viewer = User.objects.create_user(username="analysis-viewer", password="pass")
    outsider = User.objects.create_user(username="analysis-outsider", password="pass")
    other_org = Organization.objects.create(name="Other", slug="analysis-other")
    _membership(viewer, org, TuringRole.VIEWER)
    _membership(outsider, other_org, TuringRole.VIEWER)

    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="meeting.wav",
        use_case=UseCase.MEETING,
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
    analyses = TranscriptAnalysisService().generate_default_suite(
        transcript,
        provider_code="fake",
    )
    return {
        "org": org,
        "other_org": other_org,
        "viewer": viewer,
        "outsider": outsider,
        "transcript": transcript,
        "analyses": analyses,
    }


@pytest.mark.django_db
def test_list_analyses_for_transcript(analysis_setup):
    client = APIClient()
    client.force_authenticate(user=analysis_setup["viewer"])
    transcript = analysis_setup["transcript"]

    response = client.get(f"/api/turing/v1/transcripts/{transcript.id}/analyses/")
    assert response.status_code == 200
    payload = response.data
    results = payload["results"] if isinstance(payload, dict) and "results" in payload else payload
    assert len(results) == 3
    types = {row["analysis_type"] for row in results}
    assert types == {
        AnalysisType.SUMMARY,
        AnalysisType.ACTION_ITEMS,
        AnalysisType.TOPICS,
    }
    summary = next(row for row in results if row["analysis_type"] == AnalysisType.SUMMARY)
    assert "summary" in summary["content"]
    assert summary["provider"] == "fake"
    assert summary["transcript"] == transcript.id


@pytest.mark.django_db
def test_list_analyses_filter_by_type(analysis_setup):
    client = APIClient()
    client.force_authenticate(user=analysis_setup["viewer"])
    transcript = analysis_setup["transcript"]

    response = client.get(
        f"/api/turing/v1/transcripts/{transcript.id}/analyses/",
        {"analysis_type": AnalysisType.TOPICS},
    )
    assert response.status_code == 200
    payload = response.data
    results = payload["results"] if isinstance(payload, dict) and "results" in payload else payload
    assert len(results) == 1
    assert results[0]["analysis_type"] == AnalysisType.TOPICS
    assert isinstance(results[0]["content"], list)


@pytest.mark.django_db
def test_retrieve_analysis_by_id(analysis_setup):
    client = APIClient()
    client.force_authenticate(user=analysis_setup["viewer"])
    analysis = analysis_setup["analyses"][0]

    response = client.get(f"/api/turing/v1/analyses/{analysis.id}/")
    assert response.status_code == 200
    assert response.data["id"] == str(analysis.id)
    assert response.data["analysis_type"] == analysis.analysis_type
    assert response.data["content"] == analysis.content
    assert response.data["organization"] == analysis.organization_id


@pytest.mark.django_db
def test_analyses_scoped_away_from_other_org(analysis_setup):
    client = APIClient()
    client.force_authenticate(user=analysis_setup["outsider"])
    transcript = analysis_setup["transcript"]
    analysis = analysis_setup["analyses"][0]

    nested = client.get(f"/api/turing/v1/transcripts/{transcript.id}/analyses/")
    assert nested.status_code == 404

    detail = client.get(f"/api/turing/v1/analyses/{analysis.id}/")
    assert detail.status_code == 404

    listed = client.get("/api/turing/v1/analyses/")
    assert listed.status_code == 200
    payload = listed.data
    results = payload["results"] if isinstance(payload, dict) and "results" in payload else payload
    assert results == []


@pytest.mark.django_db
def test_analyses_require_authentication(analysis_setup):
    client = APIClient()
    transcript = analysis_setup["transcript"]
    analysis = analysis_setup["analyses"][0]

    assert client.get(f"/api/turing/v1/transcripts/{transcript.id}/analyses/").status_code in {
        401,
        403,
    }
    assert client.get(f"/api/turing/v1/analyses/{analysis.id}/").status_code in {401, 403}


@pytest.mark.django_db
def test_analysis_api_is_read_only(analysis_setup):
    client = APIClient()
    client.force_authenticate(user=analysis_setup["viewer"])
    analysis = analysis_setup["analyses"][0]

    assert client.post("/api/turing/v1/analyses/", {}).status_code == 405
    assert client.patch(f"/api/turing/v1/analyses/{analysis.id}/", {}).status_code == 405
    assert client.delete(f"/api/turing/v1/analyses/{analysis.id}/").status_code == 405


@pytest.mark.django_db
def test_service_get_respects_org_scope(analysis_setup):
    service = TranscriptAnalysisService()
    analysis = analysis_setup["analyses"][0]

    found = service.get(str(analysis.id), user=analysis_setup["viewer"])
    assert found.id == analysis.id

    from turing.domain.exceptions import NotFoundError

    with pytest.raises(NotFoundError):
        service.get(str(analysis.id), user=analysis_setup["outsider"])
