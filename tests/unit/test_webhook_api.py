from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from turing.domain.enums import OutboundWebhookDeliveryStatus, TuringRole
from turing.domain.events import EventName
from turing.models import Organization, OutboxEvent, TuringMembership, WebhookDelivery, WebhookSubscription

User = get_user_model()


def _membership(user, org, role):
    return TuringMembership.objects.create(
        user=user, organization=org, role=role, is_active=True
    )


@pytest.fixture
def org(db):
    return Organization.get_default()


@pytest.fixture
def admin_user(org):
    user = User.objects.create_user(username="wh-admin", password="pass")
    _membership(user, org, TuringRole.ADMIN)
    return user


@pytest.fixture
def viewer_user(org):
    user = User.objects.create_user(username="wh-viewer", password="pass")
    _membership(user, org, TuringRole.VIEWER)
    return user


@pytest.fixture
def client_admin(admin_user):
    client = APIClient()
    client.force_authenticate(user=admin_user)
    return client


@pytest.mark.django_db
def test_webhook_crud_and_secret_once(client_admin, org):
    create = client_admin.post(
        "/api/turing/v1/webhooks/",
        {
            "name": "CRM events",
            "url": "https://example.com/hooks/turing",
            "subscribed_events": [
                EventName.TRANSCRIPT_CREATED,
                EventName.ANALYSIS_COMPLETED,
            ],
        },
        format="json",
    )
    assert create.status_code == 201
    assert "signing_secret" in create.data
    assert create.data["signing_secret"]
    assert "secret" not in create.data
    assert "secret" not in create.data["subscription"]
    sub_id = create.data["subscription"]["id"]
    secret = create.data["signing_secret"]

    detail = client_admin.get(f"/api/turing/v1/webhooks/{sub_id}/")
    assert detail.status_code == 200
    assert "signing_secret" not in detail.data
    assert "secret" not in detail.data
    assert detail.data["name"] == "CRM events"
    assert EventName.TRANSCRIPT_CREATED in detail.data["subscribed_events"]

    listed = client_admin.get("/api/turing/v1/webhooks/")
    assert listed.status_code == 200
    rows = listed.data["results"] if "results" in listed.data else listed.data
    assert any(row["id"] == sub_id for row in rows)
    for row in rows:
        assert "secret" not in row
        assert "signing_secret" not in row

    patched = client_admin.patch(
        f"/api/turing/v1/webhooks/{sub_id}/",
        {"is_active": False, "name": "CRM events (paused)"},
        format="json",
    )
    assert patched.status_code == 200
    assert patched.data["is_active"] is False
    assert patched.data["name"] == "CRM events (paused)"
    assert "signing_secret" not in patched.data

    stored = WebhookSubscription.objects.get(pk=sub_id)
    assert stored.secret == secret  # unchanged after patch

    deleted = client_admin.delete(f"/api/turing/v1/webhooks/{sub_id}/")
    assert deleted.status_code == 204
    assert not WebhookSubscription.objects.filter(pk=sub_id).exists()


@pytest.mark.django_db
def test_webhook_rejects_unknown_and_empty_events(client_admin):
    unknown = client_admin.post(
        "/api/turing/v1/webhooks/",
        {
            "name": "bad",
            "url": "https://example.com/hooks",
            "subscribed_events": ["not.a.real.event"],
        },
        format="json",
    )
    assert unknown.status_code == 400

    empty = client_admin.post(
        "/api/turing/v1/webhooks/",
        {
            "name": "bad",
            "url": "https://example.com/hooks",
            "subscribed_events": [],
        },
        format="json",
    )
    assert empty.status_code == 400


@pytest.mark.django_db
def test_webhook_org_isolation(org, admin_user):
    other = Organization.objects.create(name="Other", slug="wh-other")
    outsider = User.objects.create_user(username="wh-out", password="pass")
    _membership(outsider, other, TuringRole.ADMIN)

    client = APIClient()
    client.force_authenticate(user=admin_user)
    created = client.post(
        "/api/turing/v1/webhooks/",
        {
            "name": "Mine",
            "url": "https://example.com/a",
            "subscribed_events": ["*"],
        },
        format="json",
    )
    assert created.status_code == 201
    sub_id = created.data["subscription"]["id"]

    other_client = APIClient()
    other_client.force_authenticate(user=outsider)
    listed = other_client.get("/api/turing/v1/webhooks/")
    rows = listed.data["results"] if "results" in listed.data else listed.data
    assert all(row["id"] != sub_id for row in rows)

    detail = other_client.get(f"/api/turing/v1/webhooks/{sub_id}/")
    assert detail.status_code == 404


@pytest.mark.django_db
def test_webhook_permission_checks(viewer_user, admin_user):
    viewer = APIClient()
    viewer.force_authenticate(user=viewer_user)
    denied = viewer.get("/api/turing/v1/webhooks/")
    assert denied.status_code == 403

    create_denied = viewer.post(
        "/api/turing/v1/webhooks/",
        {
            "name": "Nope",
            "url": "https://example.com/x",
            "subscribed_events": ["*"],
        },
        format="json",
    )
    assert create_denied.status_code == 403

    admin = APIClient()
    admin.force_authenticate(user=admin_user)
    ok = admin.get("/api/turing/v1/webhooks/")
    assert ok.status_code == 200


@pytest.mark.django_db
def test_webhook_delivery_listing(client_admin, org, admin_user):
    created = client_admin.post(
        "/api/turing/v1/webhooks/",
        {
            "name": "Deliveries",
            "url": "https://example.com/d",
            "subscribed_events": [EventName.MEDIA_CREATED],
        },
        format="json",
    )
    sub_id = created.data["subscription"]["id"]
    subscription = WebhookSubscription.objects.get(pk=sub_id)
    outbox = OutboxEvent.objects.create(
        organization=org,
        event_name=EventName.MEDIA_CREATED,
        payload={"organization_id": org.id, "media_id": "m1"},
    )
    WebhookDelivery.objects.create(
        subscription=subscription,
        outbox_event=outbox,
        status=OutboundWebhookDeliveryStatus.FAILED,
        attempts=2,
        response_status_code=500,
        last_error="HTTP 500",
        response_body_preview="should-not-leak",
    )

    response = client_admin.get(f"/api/turing/v1/webhooks/{sub_id}/deliveries/")
    assert response.status_code == 200
    rows = response.data["results"] if "results" in response.data else response.data
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == OutboundWebhookDeliveryStatus.FAILED
    assert row["attempts"] == 2
    assert row["response_status_code"] == 500
    assert row["last_error"] == "HTTP 500"
    assert "response_body_preview" not in row
    assert "should-not-leak" not in str(response.data)

    viewer = User.objects.create_user(username="wh-del-viewer", password="pass")
    _membership(viewer, org, TuringRole.VIEWER)
    viewer_client = APIClient()
    viewer_client.force_authenticate(user=viewer)
    assert viewer_client.get(f"/api/turing/v1/webhooks/{sub_id}/deliveries/").status_code == 403
