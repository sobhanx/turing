from __future__ import annotations

import io

import pytest
from django.db import transaction

from turing.domain.enums import OutboxEventStatus, UseCase
from turing.domain.events import DomainEvent, EventName
from turing.events.bus import EventBus, emit_after_commit
from turing.events.outbox import OutboxDispatcher, dispatch_pending
from turing.models import OutboxEvent
from turing.services.media import MediaService
from turing.tasks.events import dispatch_outbox_events


@pytest.fixture(autouse=True)
def _clear_buses():
    EventBus.clear()
    OutboxDispatcher.clear()
    yield
    EventBus.clear()
    OutboxDispatcher.clear()


@pytest.mark.django_db(transaction=True)
def test_event_creates_outbox_row():
    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="call.wav",
        use_case=UseCase.CRM_CALL,
    )
    rows = list(OutboxEvent.objects.filter(event_name=EventName.MEDIA_CREATED))
    assert len(rows) == 1
    row = rows[0]
    assert row.status == OutboxEventStatus.PENDING
    assert row.organization_id == media.organization_id
    assert row.payload["media_id"] == str(media.id)
    assert row.payload["organization_id"] == media.organization_id
    assert "full_text" not in row.payload
    assert "content" not in row.payload


@pytest.mark.django_db(transaction=True)
def test_rollback_creates_no_outbox_event():
    before = OutboxEvent.objects.count()
    event = DomainEvent(
        name=EventName.MEDIA_CREATED,
        payload={"media_id": "x", "organization_id": 1},
    )
    with pytest.raises(RuntimeError, match="rollback"):
        with transaction.atomic():
            emit_after_commit(event)
            raise RuntimeError("rollback")
    assert OutboxEvent.objects.count() == before


@pytest.mark.django_db(transaction=True)
def test_failed_handler_does_not_break_pipeline_or_outbox():
    def boom(_event):
        raise RuntimeError("integration down")

    EventBus.subscribe(EventName.MEDIA_CREATED, boom)
    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="ok.wav",
        use_case=UseCase.GENERIC,
    )
    assert media.id is not None
    assert OutboxEvent.objects.filter(
        event_name=EventName.MEDIA_CREATED,
        payload__media_id=str(media.id),
    ).exists()


@pytest.mark.django_db(transaction=True)
def test_outbox_status_transitions_delivered():
    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="meeting.wav",
        use_case=UseCase.MEETING,
    )
    row = OutboxEvent.objects.get(
        event_name=EventName.MEDIA_CREATED,
        payload__media_id=str(media.id),
    )
    assert row.status == OutboxEventStatus.PENDING

    seen: list[str] = []

    def capture(outbox_event):
        seen.append(outbox_event.event_name)
        assert outbox_event.status == OutboxEventStatus.PROCESSING

    OutboxDispatcher.subscribe("*", capture)
    counts = dispatch_pending()
    assert counts["claimed"] >= 1
    assert counts["delivered"] >= 1
    assert counts["failed"] == 0

    row.refresh_from_db()
    assert row.status == OutboxEventStatus.DELIVERED
    assert row.delivered_at is not None
    assert row.attempts == 1
    assert row.last_error == ""
    assert EventName.MEDIA_CREATED in seen


@pytest.mark.django_db(transaction=True)
def test_outbox_handler_failure_does_not_fail_outbox():
    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="fail.wav",
        use_case=UseCase.GENERIC,
    )
    row = OutboxEvent.objects.get(
        event_name=EventName.MEDIA_CREATED,
        payload__media_id=str(media.id),
    )

    def boom(_outbox_event):
        raise RuntimeError("dispatcher handler failed")

    OutboxDispatcher.subscribe(EventName.MEDIA_CREATED, boom)
    counts = dispatch_pending()
    assert counts["delivered"] >= 1
    assert counts["failed"] == 0

    row.refresh_from_db()
    assert row.status == OutboxEventStatus.DELIVERED
    assert row.delivered_at is not None


@pytest.mark.django_db(transaction=True)
def test_dispatch_outbox_events_celery_task(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="task.wav",
        use_case=UseCase.GENERIC,
    )
    result = dispatch_outbox_events.delay(limit=50)
    counts = result.get()
    assert counts["claimed"] >= 1
    assert counts["delivered"] >= 1
    row = OutboxEvent.objects.get(
        event_name=EventName.MEDIA_CREATED,
        payload__media_id=str(media.id),
    )
    assert row.status == OutboxEventStatus.DELIVERED
