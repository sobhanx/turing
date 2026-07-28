from __future__ import annotations

import io

import pytest
from django.db import transaction

from turing.domain.enums import UseCase
from turing.domain.events import DomainEvent, EventName
from turing.events.bus import EventBus, emit_after_commit
from turing.events.payloads import snapshot_external_references
from turing.models import ExternalReference
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


class _FakeSTTProvider:
    code = "speechmatics"

    def submit(self, request):
        return ProviderJobHandle(external_job_id="ext-events-1", provider_code=self.code)

    def get_status(self, handle):
        return ProviderJobStatus(external_job_id=handle.external_job_id, state="succeeded")

    def fetch_result(self, handle):
        return NormalizedTranscript(
            language_code="en",
            full_text="S1: Secret customer pricing details.",
            confidence_avg=0.9,
            speakers=[NormalizedSpeaker(label="S1")],
            segments=[
                NormalizedSegment(
                    sequence=0,
                    text="Secret customer pricing details.",
                    start_ms=0,
                    end_ms=1500,
                    confidence=0.9,
                    speaker_label="S1",
                )
            ],
        )


@pytest.fixture(autouse=True)
def _clear_event_bus():
    EventBus.clear()
    yield
    EventBus.clear()


@pytest.fixture
def recorded_events():
    seen: list[DomainEvent] = []

    def _capture(event: DomainEvent) -> None:
        seen.append(event)

    EventBus.subscribe("*", _capture)
    return seen


def test_bus_isolates_handler_failures(recorded_events):
    def boom(_event):
        raise RuntimeError("handler blew up")

    EventBus.subscribe(EventName.MEDIA_CREATED, boom)
    EventBus.emit(
        DomainEvent(
            name=EventName.MEDIA_CREATED,
            payload={"media_id": "x", "organization_id": 1},
        )
    )
    assert len(recorded_events) == 1


@pytest.mark.django_db(transaction=True)
def test_emit_after_commit_defers_inside_atomic(recorded_events):
    event = DomainEvent(
        name=EventName.MEDIA_CREATED,
        payload={"media_id": "x", "organization_id": 1},
    )
    with transaction.atomic():
        emit_after_commit(event)
        assert recorded_events == []
    assert len(recorded_events) == 1
    assert recorded_events[0].name == EventName.MEDIA_CREATED


@pytest.mark.django_db(transaction=True)
def test_emit_after_commit_immediate_outside_atomic(recorded_events):
    emit_after_commit(
        DomainEvent(
            name=EventName.JOB_COMPLETED,
            payload={"job_id": "j", "organization_id": 1},
        )
    )
    assert len(recorded_events) == 1


@pytest.mark.django_db(transaction=True)
def test_snapshot_external_references(db):
    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="call.wav",
        use_case=UseCase.CRM_CALL,
    )
    ExternalReference.objects.create(
        organization=media.organization,
        external_system="crm",
        external_type="deal",
        external_id="12345",
        media=media,
    )
    refs = snapshot_external_references(
        organization_id=media.organization_id,
        media_id=media.id,
    )
    assert refs == [
        {
            "external_system": "crm",
            "external_type": "deal",
            "external_id": "12345",
        }
    ]


@pytest.mark.django_db(transaction=True)
def test_media_created_event(recorded_events):
    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="call.wav",
        use_case=UseCase.CRM_CALL,
    )
    events = [e for e in recorded_events if e.name == EventName.MEDIA_CREATED]
    assert len(events) == 1
    payload = events[0].payload
    assert payload["media_id"] == str(media.id)
    assert payload["organization_id"] == media.organization_id
    assert "full_text" not in payload
    assert "content" not in payload


@pytest.mark.django_db(transaction=True)
def test_transcript_job_analysis_events(recorded_events, monkeypatch):
    monkeypatch.setattr(
        "turing.providers.registry.ProviderRegistry.get",
        lambda code: _FakeSTTProvider(),
    )
    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="meeting.wav",
        use_case=UseCase.MEETING,
    )
    ExternalReference.objects.create(
        organization=media.organization,
        external_system="crm",
        external_type="deal",
        external_id="99",
        media=media,
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

    by_name: dict[str, list[DomainEvent]] = {}
    for event in recorded_events:
        by_name.setdefault(event.name, []).append(event)

    assert EventName.MEDIA_CREATED in by_name
    assert EventName.TRANSCRIPT_CREATED in by_name
    assert EventName.JOB_COMPLETED in by_name
    assert EventName.ANALYSIS_COMPLETED in by_name

    transcript_event = by_name[EventName.TRANSCRIPT_CREATED][0]
    assert transcript_event.payload["transcript_id"] == str(transcript.id)
    assert transcript_event.payload["job_id"] == str(job.id)
    assert transcript_event.payload["media_id"] == str(media.id)
    assert "Secret" not in str(transcript_event.payload)
    assert "full_text" not in transcript_event.payload
    assert any(
        ref["external_id"] == "99"
        for ref in transcript_event.payload["external_references"]
    )

    job_event = by_name[EventName.JOB_COMPLETED][0]
    assert job_event.payload["job_id"] == str(job.id)
    assert job_event.payload["transcript_id"] == str(transcript.id)
    assert "Secret" not in str(job_event.payload)

    analysis_event = by_name[EventName.ANALYSIS_COMPLETED][0]
    assert analysis_event.payload["transcript_id"] == str(transcript.id)
    assert set(analysis_event.payload["analysis_ids"]) == {str(a.id) for a in analyses}
    assert "content" not in analysis_event.payload
    assert analysis_event.payload["provider"] == "fake"


@pytest.mark.django_db(transaction=True)
def test_idempotent_persist_does_not_reemit_transcript_created(
    recorded_events, monkeypatch
):
    monkeypatch.setattr(
        "turing.providers.registry.ProviderRegistry.get",
        lambda code: _FakeSTTProvider(),
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
    service.fetch_and_persist(str(job.id))
    first_count = sum(
        1 for e in recorded_events if e.name == EventName.TRANSCRIPT_CREATED
    )
    service.fetch_and_persist(str(job.id))
    second_count = sum(
        1 for e in recorded_events if e.name == EventName.TRANSCRIPT_CREATED
    )
    assert first_count == 1
    assert second_count == 1


@pytest.mark.django_db(transaction=True)
def test_failing_event_handler_does_not_break_media_create():
    def boom(_event):
        raise RuntimeError("integration down")

    EventBus.subscribe(EventName.MEDIA_CREATED, boom)
    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="ok.wav",
        use_case=UseCase.GENERIC,
    )
    assert media.id is not None
