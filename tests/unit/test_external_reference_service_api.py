from __future__ import annotations

import io

import pytest
from django.contrib.auth import get_user_model
from django.db import transaction

from turing.domain.enums import TuringRole, UseCase
from turing.domain.events import EventName
from turing.domain.exceptions import PermissionDeniedError, ValidationError
from turing.events.bus import EventBus
from turing.models import ExternalReference, Organization, ProcessingJob, TuringMembership
from turing.domain.enums import JobStatus
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
from turing.services.transcription import TranscriptionService
from rest_framework.test import APIClient

User = get_user_model()


class _FakeSTTProvider:
    code = "speechmatics"

    def submit(self, request):
        return ProviderJobHandle(external_job_id="ext-ref-svc", provider_code=self.code)

    def get_status(self, handle):
        return ProviderJobStatus(external_job_id=handle.external_job_id, state="succeeded")

    def fetch_result(self, handle):
        return NormalizedTranscript(
            language_code="en",
            full_text="S1: Hello.",
            confidence_avg=0.9,
            speakers=[NormalizedSpeaker(label="S1")],
            segments=[
                NormalizedSegment(
                    sequence=0,
                    text="Hello.",
                    start_ms=0,
                    end_ms=500,
                    confidence=0.9,
                    speaker_label="S1",
                )
            ],
        )


def _membership(user, org, role):
    return TuringMembership.objects.create(
        user=user, organization=org, role=role, is_active=True
    )


@pytest.fixture
def media(db):
    return MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="call.wav",
        use_case=UseCase.CRM_CALL,
    )


@pytest.fixture
def transcript(db, media, monkeypatch):
    monkeypatch.setattr(
        "turing.providers.registry.ProviderRegistry.get",
        lambda code: _FakeSTTProvider(),
    )
    job = JobOrchestrator().create_transcription_job(
        media=media,
        language_code="en",
        auto_enqueue=False,
    )
    service = TranscriptionService()
    service.submit(str(job.id))
    return service.fetch_and_persist(str(job.id))


@pytest.mark.django_db
def test_service_attach_media_and_lookup(media):
    service = ExternalReferenceService()
    ref, created = service.attach_to_media(
        media,
        external_system="crm",
        external_type="deal",
        external_id="12345",
    )
    assert created is True
    matches = service.lookup(
        organization=media.organization,
        external_system="crm",
        external_type="deal",
        external_id="12345",
    )
    assert matches.count() == 1
    assert matches.get().id == ref.id


@pytest.mark.django_db
def test_service_attach_transcript(transcript):
    ref, created = ExternalReferenceService().attach_to_transcript(
        transcript,
        external_system="crm",
        external_type="deal",
        external_id="99",
    )
    assert created is True
    assert ref.transcript_id == transcript.id
    assert ref.media_id is None


@pytest.mark.django_db
def test_service_duplicate_is_idempotent(media):
    service = ExternalReferenceService()
    first, created1 = service.attach_to_media(
        media,
        external_system="crm",
        external_type="deal",
        external_id="12345",
    )
    second, created2 = service.attach_to_media(
        media,
        external_system="crm",
        external_type="deal",
        external_id="12345",
    )
    assert created1 is True
    assert created2 is False
    assert first.id == second.id
    assert ExternalReference.objects.filter(media=media).count() == 1


@pytest.mark.django_db
def test_service_cross_organization_rejected(media):
    other = Organization.objects.create(name="Other", slug="extref-other")
    with pytest.raises(ValidationError):
        ExternalReferenceService().create_for_target(
            organization=other,
            external_system="crm",
            external_type="deal",
            external_id="1",
            media=media,
        )


@pytest.mark.django_db
def test_service_detach(media):
    service = ExternalReferenceService()
    ref, _ = service.attach_to_media(
        media,
        external_system="crm",
        external_type="deal",
        external_id="1",
    )
    service.detach(ref)
    assert not ExternalReference.objects.filter(pk=ref.pk).exists()


@pytest.mark.django_db
def test_service_permission_denied_for_viewer_write(media):
    user = User.objects.create_user(username="viewer-ext", password="pass")
    _membership(user, media.organization, TuringRole.VIEWER)
    with pytest.raises(PermissionDeniedError):
        ExternalReferenceService().attach_to_media(
            media,
            external_system="crm",
            external_type="deal",
            external_id="1",
            user=user,
        )


@pytest.mark.django_db(transaction=True)
def test_job_completed_emitted_without_attempt(monkeypatch):
    """Regression: succeed path with no attempt must still emit job.completed once."""
    monkeypatch.setattr(
        "turing.providers.registry.ProviderRegistry.get",
        lambda code: _FakeSTTProvider(),
    )
    seen = []
    EventBus.clear()
    EventBus.subscribe(EventName.JOB_COMPLETED, seen.append)

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
    # Persist transcript without going through submit (no attempt row).
    from turing.services.transcript import TranscriptService
    from turing.domain.enums import RevisionSource

    TranscriptService().persist_from_provider(
        job=job,
        normalized=_FakeSTTProvider().fetch_result(
            ProviderJobHandle(external_job_id="x", provider_code="speechmatics")
        ),
        source=RevisionSource.PROVIDER,
    )
    assert job.attempts.count() == 0
    ProcessingJob.objects.filter(pk=job.pk).update(status=JobStatus.RUNNING)
    job.refresh_from_db()
    assert JobOrchestrator().mark_succeeded(job, None) is True
    job.refresh_from_db()
    assert job.status == JobStatus.SUCCEEDED

    job_events = [e for e in seen if e.name == EventName.JOB_COMPLETED]
    assert len(job_events) == 1
    assert job_events[0].payload["job_id"] == str(job.id)
    assert job_events[0].payload["organization_id"] == job.organization_id

    # Second call must not re-emit.
    JobOrchestrator().mark_succeeded(job, None)
    assert len([e for e in seen if e.name == EventName.JOB_COMPLETED]) == 1
    EventBus.clear()


@pytest.mark.django_db(transaction=True)
def test_fetch_persist_without_attempt_emits_job_completed(monkeypatch):
    monkeypatch.setattr(
        "turing.providers.registry.ProviderRegistry.get",
        lambda code: _FakeSTTProvider(),
    )
    seen = []
    EventBus.clear()
    EventBus.subscribe(EventName.JOB_COMPLETED, seen.append)

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
    # Simulate provider job id without creating an attempt.
    ProcessingJob.objects.filter(pk=job.pk).update(
        external_job_id="ext-no-attempt",
        status=JobStatus.RUNNING,
    )
    TranscriptionService().fetch_and_persist(str(job.id))
    job.refresh_from_db()
    assert job.status == JobStatus.SUCCEEDED
    assert job.attempts.count() == 0
    assert len([e for e in seen if e.name == EventName.JOB_COMPLETED]) == 1
    EventBus.clear()


@pytest.mark.django_db
def test_api_media_external_references_and_filter(media):
    org = media.organization
    editor = User.objects.create_user(username="ext-editor", password="pass")
    outsider = User.objects.create_user(username="ext-out", password="pass")
    other = Organization.objects.create(name="Beta", slug="ext-beta")
    _membership(editor, org, TuringRole.EDITOR)
    _membership(outsider, other, TuringRole.EDITOR)

    client = APIClient()
    client.force_authenticate(user=editor)

    create = client.post(
        f"/api/turing/v1/media/{media.id}/external-references/",
        {
            "external_system": "crm",
            "external_type": "deal",
            "external_id": "123",
        },
        format="json",
    )
    assert create.status_code == 201
    ref_id = create.data["id"]

    listed = client.get(f"/api/turing/v1/media/{media.id}/external-references/")
    assert listed.status_code == 200
    results = listed.data["results"] if "results" in listed.data else listed.data
    assert len(results) == 1

    detail = client.get(f"/api/turing/v1/media/{media.id}/")
    assert detail.status_code == 200
    assert detail.data["external_references"][0]["external_id"] == "123"

    filtered = client.get(
        "/api/turing/v1/media/",
        {
            "external_system": "crm",
            "external_type": "deal",
            "external_id": "123",
        },
    )
    assert filtered.status_code == 200
    rows = filtered.data["results"] if "results" in filtered.data else filtered.data
    assert any(str(row["id"]) == str(media.id) for row in rows)

    client.force_authenticate(user=outsider)
    deny = client.get(f"/api/turing/v1/media/{media.id}/external-references/")
    assert deny.status_code == 404
    deny_del = client.delete(f"/api/turing/v1/external-references/{ref_id}/")
    assert deny_del.status_code == 404

    client.force_authenticate(user=editor)
    deleted = client.delete(f"/api/turing/v1/external-references/{ref_id}/")
    assert deleted.status_code == 204


@pytest.mark.django_db
def test_api_transcript_external_references(transcript):
    editor = User.objects.create_user(username="tx-editor", password="pass")
    _membership(editor, transcript.organization, TuringRole.EDITOR)
    client = APIClient()
    client.force_authenticate(user=editor)

    create = client.post(
        f"/api/turing/v1/transcripts/{transcript.id}/external-references/",
        {
            "external_system": "crm",
            "external_type": "deal",
            "external_id": "abc",
        },
        format="json",
    )
    assert create.status_code == 201

    filtered = client.get(
        "/api/turing/v1/transcripts/",
        {
            "external_system": "crm",
            "external_type": "deal",
            "external_id": "abc",
        },
    )
    assert filtered.status_code == 200
    rows = filtered.data["results"] if "results" in filtered.data else filtered.data
    assert any(str(row["id"]) == str(transcript.id) for row in rows)
