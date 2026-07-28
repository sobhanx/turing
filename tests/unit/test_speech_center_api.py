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
from turing.services.external_reference import ExternalReferenceService
from turing.services.job_orchestrator import JobOrchestrator
from turing.services.media import MediaService
from turing.services.transcript_analysis import TranscriptAnalysisService
from turing.services.transcription import TranscriptionService

User = get_user_model()
BASE = "/api/turing/v1/speech-center/"


class _FakeSTTProvider:
    code = "speechmatics"

    def submit(self, request):
        return ProviderJobHandle(external_job_id="ext-sc", provider_code=self.code)

    def get_status(self, handle):
        return ProviderJobStatus(external_job_id=handle.external_job_id, state="succeeded")

    def fetch_result(self, handle):
        return NormalizedTranscript(
            language_code="en",
            full_text="S1: Discuss renewal and follow up.",
            confidence_avg=0.91,
            speakers=[NormalizedSpeaker(label="S1")],
            segments=[
                NormalizedSegment(
                    sequence=0,
                    text="Discuss renewal and follow up.",
                    start_ms=0,
                    end_ms=2500,
                    confidence=0.91,
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
def speech_setup(db, monkeypatch):
    monkeypatch.setattr(
        "turing.providers.registry.ProviderRegistry.get",
        lambda code: _FakeSTTProvider(),
    )
    org = Organization.get_default()
    viewer = User.objects.create_user(username="sc-viewer", password="pass")
    outsider = User.objects.create_user(username="sc-outsider", password="pass")
    other_org = Organization.objects.create(name="Other SC", slug="sc-other")
    _membership(viewer, org, TuringRole.VIEWER)
    _membership(outsider, other_org, TuringRole.VIEWER)

    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="deal-call.wav",
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
    TranscriptAnalysisService().generate_default_suite(
        transcript,
        provider_code="fake",
    )
    ExternalReferenceService().attach_to_media(
        media,
        external_system="salesforce",
        external_type="call",
        external_id="SF-CALL-1",
    )
    ExternalReferenceService().attach_to_transcript(
        transcript,
        external_system="salesforce",
        external_type="call",
        external_id="SF-CALL-1",
    )
    return {
        "org": org,
        "other_org": other_org,
        "viewer": viewer,
        "outsider": outsider,
        "media": media,
        "transcript": transcript,
    }


@pytest.fixture
def viewer_client(speech_setup):
    client = APIClient()
    client.force_authenticate(user=speech_setup["viewer"])
    return client


@pytest.mark.django_db
def test_external_lookup_aggregates_analyses(viewer_client, speech_setup):
    response = viewer_client.get(
        BASE,
        {
            "external_system": "salesforce",
            "external_type": "call",
            "external_id": "SF-CALL-1",
        },
    )
    assert response.status_code == 200
    data = response.data
    assert set(data.keys()) == {
        "media",
        "transcript",
        "status",
        "speakers",
        "analyses",
        "external_references",
    }
    assert data["media"]["id"] == str(speech_setup["media"].id)
    assert data["transcript"]["id"] == str(speech_setup["transcript"].id)
    assert data["status"] == speech_setup["transcript"].status
    assert len(data["speakers"]) >= 1
    assert data["analyses"]["summary"] is not None
    assert data["analyses"]["topics"] is not None
    assert data["analyses"]["action_items"] is not None
    assert data["analyses"]["summary"]["analysis_type"] == AnalysisType.SUMMARY
    assert "content" in data["analyses"]["summary"]
    systems = {(r["external_system"], r["external_type"], r["external_id"]) for r in data["external_references"]}
    assert ("salesforce", "call", "SF-CALL-1") in systems


@pytest.mark.django_db
def test_timeline_endpoint(viewer_client, speech_setup):
    tid = speech_setup["transcript"].id
    response = viewer_client.get(f"{BASE}{tid}/timeline/")
    assert response.status_code == 200
    data = response.data
    assert data["transcript_id"] == str(tid)
    assert len(data["segments"]) >= 1
    assert data["segments"][0]["start_ms"] == 0
    assert data["timestamps"]["start_ms"] == 0
    assert data["timestamps"]["end_ms"] == 2500
    types = {row["analysis_type"] for row in data["analysis_references"]}
    assert AnalysisType.SUMMARY in types
    assert AnalysisType.TOPICS in types
    assert AnalysisType.ACTION_ITEMS in types


@pytest.mark.django_db
def test_org_isolation(speech_setup):
    outsider = APIClient()
    outsider.force_authenticate(user=speech_setup["outsider"])
    listed = outsider.get(
        BASE,
        {
            "external_system": "salesforce",
            "external_type": "call",
            "external_id": "SF-CALL-1",
        },
    )
    assert listed.status_code == 404

    timeline = outsider.get(f"{BASE}{speech_setup['transcript'].id}/timeline/")
    assert timeline.status_code == 404


@pytest.mark.django_db
def test_permission_requires_auth(speech_setup):
    anon = APIClient()
    response = anon.get(
        BASE,
        {
            "external_system": "salesforce",
            "external_type": "call",
            "external_id": "SF-CALL-1",
        },
    )
    assert response.status_code in {401, 403}


@pytest.mark.django_db
def test_missing_query_params(viewer_client):
    response = viewer_client.get(BASE)
    assert response.status_code == 400
    assert "external_system" in str(response.data).lower()


@pytest.mark.django_db
def test_missing_external_reference(viewer_client):
    response = viewer_client.get(
        BASE,
        {
            "external_system": "salesforce",
            "external_type": "call",
            "external_id": "does-not-exist",
        },
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_missing_transcript_media_only(viewer_client, speech_setup):
    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"pending"),
        filename="pending.wav",
        use_case=UseCase.MEETING,
        organization=speech_setup["org"],
    )
    ExternalReferenceService().attach_to_media(
        media,
        external_system="zoom",
        external_type="meeting",
        external_id="ZOOM-PENDING-1",
    )
    response = viewer_client.get(
        BASE,
        {
            "external_system": "zoom",
            "external_type": "meeting",
            "external_id": "ZOOM-PENDING-1",
        },
    )
    assert response.status_code == 200
    assert response.data["media"]["id"] == str(media.id)
    assert response.data["transcript"] is None
    assert response.data["status"] is None
    assert response.data["speakers"] == []
    assert response.data["analyses"]["summary"] is None
    assert response.data["analyses"]["topics"] is None
    assert response.data["analyses"]["action_items"] is None


@pytest.mark.django_db
def test_timeline_missing_transcript(viewer_client):
    response = viewer_client.get(
        f"{BASE}00000000-0000-0000-0000-000000000001/timeline/"
    )
    assert response.status_code == 404
