from __future__ import annotations

from datetime import timedelta

import pytest
import responses
from django.utils import timezone

from turing.celery_schedule import build_celery_beat_schedule
from turing.domain.enums import OutboundWebhookDeliveryStatus, OutboxEventStatus
from turing.domain.events import DomainEvent, EventName
from turing.events.outbox import persist_domain_event
from turing.models import Organization, OutboxEvent, WebhookDelivery, WebhookSubscription
from turing.services.outbox_ops import OutboxOpsService
from turing.services.webhook_delivery import WebhookDeliveryService
from turing.services.webhook_retry import is_retryable_failure, is_retryable_http_status
from turing.tasks.events import recover_stuck_outbox_work


@pytest.fixture
def org(db):
    return Organization.get_default()


def _subscription(org, **kwargs) -> WebhookSubscription:
    defaults = {
        "organization": org,
        "name": "Ops CRM",
        "url": "https://hooks.example.com/ops",
        "secret": "secret",
        "subscribed_events": ["*"],
        "is_active": True,
    }
    defaults.update(kwargs)
    sub = WebhookSubscription(**defaults)
    sub.full_clean()
    sub.save()
    return sub


def _outbox(org, *, event_name=EventName.MEDIA_CREATED) -> OutboxEvent:
    row = persist_domain_event(
        DomainEvent(
            name=event_name,
            payload={
                "organization_id": org.id,
                "media_id": "22222222-2222-2222-2222-222222222222",
            },
        )
    )
    assert row is not None
    return row


@pytest.mark.django_db
def test_stuck_outbox_processing_recovery(org, settings):
    settings.TURING_OUTBOX_STUCK_TIMEOUT_SECONDS = 60
    from turing.conf import clear_settings_cache

    clear_settings_cache()

    event = _outbox(org)
    event.status = OutboxEventStatus.PROCESSING
    event.processing_started_at = timezone.now() - timedelta(seconds=120)
    event.save(update_fields=["status", "processing_started_at", "updated_at"])

    ops = OutboxOpsService()
    assert ops.stuck_outbox_events().filter(pk=event.pk).exists()
    counts = ops.recover_stuck()
    assert counts["outbox_events"] == 1

    event.refresh_from_db()
    assert event.status == OutboxEventStatus.PENDING
    assert event.processing_started_at is None
    assert event.recovery_count == 1
    assert "Recovered from stuck PROCESSING" in event.last_error


@pytest.mark.django_db
def test_stuck_webhook_delivering_recovery(org, settings, monkeypatch):
    settings.TURING_OUTBOX_STUCK_TIMEOUT_SECONDS = 60
    from turing.conf import clear_settings_cache

    clear_settings_cache()

    requeued: list[str] = []
    monkeypatch.setattr(
        "turing.tasks.webhooks.deliver_webhook_delivery.delay",
        lambda delivery_id: requeued.append(delivery_id),
    )

    sub = _subscription(org)
    outbox = _outbox(org)
    delivery = WebhookDelivery.objects.create(
        subscription=sub,
        outbox_event=outbox,
        status=OutboundWebhookDeliveryStatus.DELIVERING,
        processing_started_at=timezone.now() - timedelta(minutes=10),
        attempts=1,
    )

    ops = OutboxOpsService()
    assert ops.stuck_deliveries().filter(pk=delivery.pk).exists()
    counts = ops.recover_stuck_deliveries()
    assert counts == 1

    delivery.refresh_from_db()
    assert delivery.status == OutboundWebhookDeliveryStatus.PENDING
    assert delivery.processing_started_at is None
    assert delivery.recovery_count == 1
    assert str(delivery.id) in requeued


@pytest.mark.parametrize(
    ("status_code", "network_error", "expected"),
    [
        (None, True, True),
        (429, False, True),
        (500, False, True),
        (503, False, True),
        (400, False, False),
        (401, False, False),
        (403, False, False),
        (404, False, False),
        (422, False, False),
    ],
)
def test_retry_decision(status_code, network_error, expected):
    assert is_retryable_http_status(status_code) is (
        expected if status_code is not None else True
    )
    assert (
        is_retryable_failure(status_code=status_code, network_error=network_error)
        is expected
    )


@pytest.mark.django_db
@responses.activate
def test_non_retryable_http_fails_immediately(org, settings):
    settings.TURING_OUTBOUND_WEBHOOK_MAX_RETRIES = 5
    from turing.conf import clear_settings_cache

    clear_settings_cache()

    sub = _subscription(org)
    outbox = _outbox(org)
    delivery = WebhookDelivery.objects.create(
        subscription=sub,
        outbox_event=outbox,
        status=OutboundWebhookDeliveryStatus.PENDING,
    )
    responses.add(responses.POST, sub.url, status=404, body="missing")

    outcome = WebhookDeliveryService().attempt_delivery(str(delivery.id))
    assert outcome == "failed"
    delivery.refresh_from_db()
    assert delivery.status == OutboundWebhookDeliveryStatus.FAILED
    assert delivery.attempts == 1
    assert delivery.response_status_code == 404
    assert "non-retryable" in delivery.last_error


@pytest.mark.django_db
@responses.activate
def test_retryable_http_schedules_retry(org, settings):
    settings.TURING_OUTBOUND_WEBHOOK_MAX_RETRIES = 5
    from turing.conf import clear_settings_cache

    clear_settings_cache()

    sub = _subscription(org)
    outbox = _outbox(org)
    delivery = WebhookDelivery.objects.create(
        subscription=sub,
        outbox_event=outbox,
        status=OutboundWebhookDeliveryStatus.PENDING,
    )
    responses.add(responses.POST, sub.url, status=503, body="busy")

    outcome = WebhookDeliveryService().attempt_delivery(str(delivery.id))
    assert outcome == "retry"
    delivery.refresh_from_db()
    assert delivery.status == OutboundWebhookDeliveryStatus.PENDING
    assert delivery.attempts == 1
    assert delivery.processing_started_at is None


def test_beat_task_registration_when_enabled(settings):
    settings.TURING_OUTBOX_DISPATCH_ENABLED = True
    settings.TURING_OUTBOX_DISPATCH_INTERVAL_SECONDS = 45
    schedule = build_celery_beat_schedule()
    assert "turing-dispatch-outbox-events" in schedule
    assert (
        schedule["turing-dispatch-outbox-events"]["task"]
        == "turing.tasks.events.dispatch_outbox_events"
    )
    assert schedule["turing-dispatch-outbox-events"]["schedule"] == 45.0
    assert "turing-recover-stuck-outbox" in schedule
    assert (
        schedule["turing-recover-stuck-outbox"]["task"]
        == "turing.tasks.events.recover_stuck_outbox_work"
    )


def test_beat_task_registration_disabled(settings):
    settings.TURING_OUTBOX_DISPATCH_ENABLED = False
    assert build_celery_beat_schedule() == {}


@pytest.mark.django_db
def test_failed_delivery_visibility(org):
    sub = _subscription(org)
    outbox = _outbox(org)
    failed = WebhookDelivery.objects.create(
        subscription=sub,
        outbox_event=outbox,
        status=OutboundWebhookDeliveryStatus.FAILED,
        attempts=3,
        last_error="HTTP 500",
    )
    pending = WebhookDelivery.objects.create(
        subscription=_subscription(org, name="Other", url="https://hooks.example.com/other"),
        outbox_event=_outbox(org, event_name=EventName.JOB_COMPLETED),
        status=OutboundWebhookDeliveryStatus.PENDING,
    )

    ops = OutboxOpsService()
    assert list(ops.failed_deliveries().values_list("id", flat=True)) == [failed.id]
    assert list(ops.pending_deliveries().values_list("id", flat=True)) == [pending.id]
    assert ops.failed_deliveries(organization_id=org.id).count() == 1


@pytest.mark.django_db
def test_recover_stuck_outbox_work_task(org, settings, monkeypatch):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.TURING_OUTBOX_STUCK_TIMEOUT_SECONDS = 30
    from turing.conf import clear_settings_cache

    clear_settings_cache()
    monkeypatch.setattr(
        "turing.tasks.webhooks.deliver_webhook_delivery.delay",
        lambda *_a, **_k: None,
    )

    event = _outbox(org)
    event.status = OutboxEventStatus.PROCESSING
    event.processing_started_at = timezone.now() - timedelta(minutes=5)
    event.save(update_fields=["status", "processing_started_at", "updated_at"])

    result = recover_stuck_outbox_work.delay()
    counts = result.get()
    assert counts["outbox_events"] == 1
    event.refresh_from_db()
    assert event.status == OutboxEventStatus.PENDING
