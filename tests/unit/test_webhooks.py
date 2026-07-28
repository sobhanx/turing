from __future__ import annotations

import hashlib
import hmac
import json

import pytest
import responses
from django.core.exceptions import ValidationError

from turing.domain.enums import OutboundWebhookDeliveryStatus, OutboxEventStatus
from turing.domain.events import EventName
from turing.events.outbox import OutboxDispatcher, dispatch_pending, persist_domain_event
from turing.events.outbound import register_outbound_handlers
from turing.models import Organization, OutboxEvent, WebhookDelivery, WebhookSubscription
from turing.domain.events import DomainEvent
from turing.services.webhook_delivery import (
    EVENT_HEADER,
    SIGNATURE_HEADER,
    WebhookDeliveryService,
    build_webhook_envelope,
    sign_payload,
)
from turing.tasks.events import dispatch_outbox_events


@pytest.fixture(autouse=True)
def _webhook_handlers(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True
    OutboxDispatcher.clear()
    register_outbound_handlers()
    yield
    OutboxDispatcher.clear()


def _subscription(**kwargs) -> WebhookSubscription:
    org = kwargs.pop("organization", None) or Organization.get_default()
    defaults = {
        "organization": org,
        "name": "Host CRM",
        "url": "https://hooks.example.com/turing",
        "secret": "super-secret",
        "subscribed_events": [EventName.TRANSCRIPT_CREATED, EventName.MEDIA_CREATED],
        "is_active": True,
    }
    defaults.update(kwargs)
    sub = WebhookSubscription(**defaults)
    sub.full_clean()
    sub.save()
    return sub


def _outbox(*, org=None, event_name=EventName.MEDIA_CREATED, **payload) -> OutboxEvent:
    organization = org or Organization.get_default()
    data = {
        "organization_id": organization.id,
        "media_id": "11111111-1111-1111-1111-111111111111",
        "external_references": [],
    }
    data.update(payload)
    return persist_domain_event(
        DomainEvent(name=event_name, payload=data)
    )


@pytest.mark.django_db
def test_subscription_filtering_by_event_and_active():
    org = Organization.get_default()
    matching = _subscription(
        name="match",
        subscribed_events=[EventName.MEDIA_CREATED],
    )
    _subscription(
        name="other-event",
        subscribed_events=[EventName.ANALYSIS_COMPLETED],
    )
    _subscription(
        name="inactive",
        subscribed_events=["*"],
        is_active=False,
    )
    wildcard = _subscription(
        name="wildcard",
        subscribed_events=["*"],
    )
    service = WebhookDeliveryService()
    found = service.matching_subscriptions(
        organization_id=org.id,
        event_name=EventName.MEDIA_CREATED,
    )
    ids = {s.id for s in found}
    assert matching.id in ids
    assert wildcard.id in ids
    assert len(found) == 2


@pytest.mark.django_db
def test_subscription_url_validation():
    org = Organization.get_default()
    sub = WebhookSubscription(
        organization=org,
        name="bad",
        url="ftp://not-allowed.example",
        secret="x",
        subscribed_events=["*"],
    )
    with pytest.raises(ValidationError):
        sub.full_clean()


@pytest.mark.django_db
def test_signature_generation():
    body = b'{"event":"media.created"}'
    sig = sign_payload("super-secret", body)
    expected = "sha256=" + hmac.new(
        b"super-secret",
        body,
        hashlib.sha256,
    ).hexdigest()
    assert sig == expected


@pytest.mark.django_db
@responses.activate
def test_successful_delivery():
    sub = _subscription()
    outbox = _outbox()
    assert outbox is not None

    def _check(request):
        assert request.headers[EVENT_HEADER] == EventName.MEDIA_CREATED
        body = request.body
        expected = sign_payload(sub.secret, body if isinstance(body, bytes) else body.encode())
        assert request.headers[SIGNATURE_HEADER] == expected
        payload = json.loads(body)
        assert payload["event"] == EventName.MEDIA_CREATED
        assert payload["id"] == str(outbox.id)
        assert payload["organization_id"] == str(outbox.organization_id)
        assert "full_text" not in payload["data"]
        assert "content" not in payload["data"]
        assert "secret" not in json.dumps(payload)
        return (200, {}, '{"ok":true}')

    responses.add_callback(
        responses.POST,
        sub.url,
        callback=_check,
        content_type="application/json",
    )

    deliveries = WebhookDeliveryService().enqueue_for_outbox(outbox)
    assert len(deliveries) == 1
    delivery = WebhookDelivery.objects.get(pk=deliveries[0].pk)
    assert delivery.status == OutboundWebhookDeliveryStatus.DELIVERED
    assert delivery.response_status_code == 200
    assert delivery.attempts == 1
    assert delivery.delivered_at is not None


@pytest.mark.django_db
@responses.activate
def test_failed_delivery_marks_failed_after_retries(settings):
    settings.TURING_OUTBOUND_WEBHOOK_MAX_RETRIES = 2
    from turing.conf import clear_settings_cache

    clear_settings_cache()

    sub = _subscription()
    outbox = _outbox()
    responses.add(responses.POST, sub.url, status=500, body="nope")

    # Disable eager retry explosions: call attempt loop manually.
    service = WebhookDeliveryService()
    delivery = WebhookDelivery.objects.create(
        subscription=sub,
        outbox_event=outbox,
        status=OutboundWebhookDeliveryStatus.PENDING,
    )
    outcomes = []
    for _ in range(5):
        outcomes.append(service.attempt_delivery(str(delivery.id)))
        delivery.refresh_from_db()
        if delivery.status == OutboundWebhookDeliveryStatus.FAILED:
            break
    assert OutboundWebhookDeliveryStatus.FAILED == delivery.status
    assert "retry" in outcomes
    assert delivery.attempts >= 3
    assert delivery.last_error
    assert delivery.delivered_at is None


@pytest.mark.django_db
@responses.activate
def test_retry_backoff_countdown(settings):
    settings.TURING_OUTBOUND_WEBHOOK_BACKOFF_BASE_SECONDS = 2
    settings.TURING_OUTBOUND_WEBHOOK_BACKOFF_MAX_SECONDS = 10
    from turing.conf import clear_settings_cache

    clear_settings_cache()
    service = WebhookDeliveryService()
    assert service.retry_countdown(1) == 2
    assert service.retry_countdown(2) == 4
    assert service.retry_countdown(3) == 8
    assert service.retry_countdown(4) == 10


@pytest.mark.django_db
@responses.activate
def test_org_isolation():
    org_a = Organization.get_default()
    org_b = Organization.objects.create(name="Beta", slug="webhook-beta")
    sub_a = _subscription(organization=org_a, name="A")
    sub_b = _subscription(organization=org_b, name="B", url="https://hooks.example.com/b")

    responses.add(responses.POST, sub_a.url, json={"ok": True}, status=200)
    responses.add(responses.POST, sub_b.url, json={"ok": True}, status=200)

    outbox_a = _outbox(org=org_a)
    WebhookDeliveryService().enqueue_for_outbox(outbox_a)

    assert WebhookDelivery.objects.filter(subscription=sub_a).count() == 1
    assert WebhookDelivery.objects.filter(subscription=sub_b).count() == 0


@pytest.mark.django_db(transaction=True)
@responses.activate
def test_outbox_dispatch_enqueues_without_failing_outbox_on_http_error(settings):
    settings.TURING_OUTBOUND_WEBHOOK_MAX_RETRIES = 0
    from turing.conf import clear_settings_cache

    clear_settings_cache()

    sub = _subscription()
    responses.add(responses.POST, sub.url, status=503, body="down")

    outbox = _outbox()
    assert outbox.status == OutboxEventStatus.PENDING

    counts = dispatch_pending()
    assert counts["delivered"] == 1
    assert counts["failed"] == 0

    outbox.refresh_from_db()
    assert outbox.status == OutboxEventStatus.DELIVERED

    delivery = WebhookDelivery.objects.get(outbox_event=outbox, subscription=sub)
    # Eager celery may have already exhausted retries.
    assert delivery.status in {
        OutboundWebhookDeliveryStatus.FAILED,
        OutboundWebhookDeliveryStatus.PENDING,
        OutboundWebhookDeliveryStatus.DELIVERED,
    }
    # With max_retries=0 → one attempt then failed.
    assert delivery.status == OutboundWebhookDeliveryStatus.FAILED
    assert delivery.attempts == 1


@pytest.mark.django_db
def test_envelope_strips_forbidden_keys():
    org = Organization.get_default()
    outbox = OutboxEvent.objects.create(
        organization=org,
        event_name=EventName.TRANSCRIPT_CREATED,
        payload={
            "organization_id": org.id,
            "transcript_id": "t1",
            "full_text": "SECRET TEXT",
            "content": {"summary": "nope"},
        },
    )
    envelope = build_webhook_envelope(outbox)
    assert "full_text" not in envelope["data"]
    assert "content" not in envelope["data"]
    assert envelope["data"]["transcript_id"] == "t1"
    assert envelope["event"] == EventName.TRANSCRIPT_CREATED


@pytest.mark.django_db(transaction=True)
@responses.activate
def test_dispatch_outbox_events_task_fanout():
    sub = _subscription(subscribed_events=["*"])
    responses.add(responses.POST, sub.url, json={"ok": True}, status=200)
    outbox = _outbox()
    result = dispatch_outbox_events.delay(limit=50)
    counts = result.get()
    assert counts["delivered"] >= 1
    delivery = WebhookDelivery.objects.get(outbox_event=outbox)
    assert delivery.status == OutboundWebhookDeliveryStatus.DELIVERED
