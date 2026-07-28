from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from turing.connectors import (
    BaseConnector,
    ConnectorConfigurationError,
    ConnectorRegistry,
    ConnectorSyncResult,
    InstallationRequirementField,
    InstallationRequirements,
    MediaPullItem,
)
from turing.domain.enums import (
    ConnectorInstallationStatus,
    ConnectorSyncJobStatus,
    TuringRole,
)
from turing.domain.events import DomainEvent, EventName
from turing.events.bus import EventBus
from turing.models import (
    ConnectorCredential,
    ConnectorInstallation,
    ConnectorSyncJob,
    Organization,
    TuringMembership,
)
from turing.services.connector_installation import ConnectorInstallationService

User = get_user_model()
BASE = "/api/turing/v1/connector-installations/"


class ApiV2FakeConnector(BaseConnector):
    connector_type = "api-v2-fake"
    display_name = "API V2 Fake"
    supported_sync_types = ("media",)
    installation_requirements = InstallationRequirements(
        config_fields=(
            InstallationRequirementField(
                key="account_id",
                label="Account ID",
                validation_message="account_id is required.",
            ),
            InstallationRequirementField(
                key="api_token",
                label="API token",
                secret=True,
                validation_message="API token is required.",
            ),
        ),
        messages=("api_credentials",),
    )

    def validate_config(self) -> None:
        if not self.config.get("account_id"):
            raise ConnectorConfigurationError("account_id is required.")

    def health_check(self) -> dict[str, Any]:
        return {"ok": True}

    def pull_media(self, **kwargs: Any) -> list[MediaPullItem]:
        return []

    def sync(self) -> ConnectorSyncResult:
        self.validate_config()
        return ConnectorSyncResult(records_processed=0)


def _membership(user, org, role):
    return TuringMembership.objects.create(
        user=user, organization=org, role=role, is_active=True
    )


@pytest.fixture(autouse=True)
def _register_fake():
    from turing.connectors.builtins import register_builtin_connectors

    ConnectorRegistry.clear()
    ConnectorRegistry.register(ApiV2FakeConnector)
    EventBus.clear()
    yield
    ConnectorRegistry.clear()
    register_builtin_connectors()
    EventBus.clear()


@pytest.fixture
def org(db):
    return Organization.get_default()


@pytest.fixture
def admin_user(org):
    user = User.objects.create_user(username="inst-v2-admin", password="pass")
    _membership(user, org, TuringRole.ADMIN)
    return user


@pytest.fixture
def viewer_user(org):
    user = User.objects.create_user(username="inst-v2-viewer", password="pass")
    _membership(user, org, TuringRole.VIEWER)
    return user


@pytest.fixture
def client_admin(admin_user):
    client = APIClient()
    client.force_authenticate(user=admin_user)
    return client


@pytest.fixture
def installation(org):
    return ConnectorInstallation.objects.create(
        organization=org,
        connector_type="api-v2-fake",
        name="UX Install",
        status=ConnectorInstallationStatus.PENDING,
        config={"account_id": "acc-1", "api_token": "super-secret-token"},
    )


@pytest.mark.django_db
def test_serializer_fields_and_no_secrets(client_admin, installation):
    response = client_admin.get(f"{BASE}{installation.id}/")
    assert response.status_code == 200
    data = response.data
    assert set(data.keys()) == {
        "id",
        "connector_type",
        "name",
        "status",
        "auth_status",
        "health",
        "last_sync",
        "created_at",
        "updated_at",
    }
    assert data["last_sync"] is None
    assert data["health"]["current_health"] == "pending"
    assert data["auth_status"]["has_credentials"] is True
    blob = str(data).lower()
    assert "config" not in data
    assert "super-secret-token" not in blob
    assert "api_token" not in blob
    assert "encrypted" not in blob
    assert "credential" not in blob or "has_credentials" in blob


@pytest.mark.django_db
def test_last_sync_and_health_visibility(client_admin, installation):
    ConnectorInstallationService().activate(installation)
    installation.refresh_from_db()
    job = ConnectorSyncJob.objects.create(
        installation=installation,
        status=ConnectorSyncJobStatus.COMPLETED,
        started_at=timezone.now() - timedelta(minutes=5),
        finished_at=timezone.now() - timedelta(minutes=4),
        records_processed=3,
    )
    response = client_admin.get(f"{BASE}{installation.id}/")
    assert response.status_code == 200
    assert response.data["health"]["current_health"] == "healthy"
    assert response.data["health"]["last_successful_sync_at"] is not None
    assert response.data["last_sync"]["id"] == str(job.id)
    assert response.data["last_sync"]["status"] == ConnectorSyncJobStatus.COMPLETED
    assert response.data["last_sync"]["records_processed"] == 3


@pytest.mark.django_db
def test_catalog_ux_contract(client_admin):
    response = client_admin.get("/api/turing/v1/connectors/")
    assert response.status_code == 200
    by_type = {row["connector_type"]: row for row in response.data}
    row = by_type["api-v2-fake"]
    assert row["display_name"] == "API V2 Fake"
    assert row["auth_type"] == "api_key"
    assert row["provider"] == ""
    assert row["capabilities"] == {
        "oauth": False,
        "refresh": False,
        "revoke": False,
    }
    assert row["supported_sync_types"] == ["media"]
    assert row["installation_requirements"]["messages"] == ["api_credentials"]
    assert [
        field["key"] for field in row["installation_requirements"]["config_fields"]
    ] == ["account_id", "api_token"]
    assert row["installation_requirements"]["config_fields"][1]["secret"] is True
    assert "super-secret" not in str(response.data)
    assert "password" not in str(response.data).lower()


@pytest.mark.django_db(transaction=True)
def test_activate_and_revoke_actions_emit_events(client_admin, installation):
    seen: list[DomainEvent] = []
    EventBus.subscribe("*", seen.append)

    activate = client_admin.post(f"{BASE}{installation.id}/activate/")
    assert activate.status_code == 200
    assert activate.data["status"] == ConnectorInstallationStatus.ACTIVE
    assert "config" not in activate.data
    assert EventName.CONNECTOR_INSTALLATION_ACTIVATED in [e.name for e in seen]

    revoke = client_admin.post(f"{BASE}{installation.id}/revoke/")
    assert revoke.status_code == 200
    assert revoke.data["status"] == ConnectorInstallationStatus.REVOKED
    assert revoke.data["auth_status"]["status"] == ConnectorInstallationStatus.REVOKED
    assert EventName.CONNECTOR_INSTALLATION_REVOKED in [e.name for e in seen]

    for event in seen:
        assert "super-secret-token" not in str(event.payload)


@pytest.mark.django_db
def test_activate_revoked_fails(client_admin, installation):
    ConnectorInstallationService().revoke(installation)
    response = client_admin.post(f"{BASE}{installation.id}/activate/")
    assert response.status_code == 400


@pytest.mark.django_db(transaction=True)
def test_sync_action_still_works(client_admin, installation, settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    ConnectorInstallationService().activate(installation)
    response = client_admin.post(f"{BASE}{installation.id}/sync/")
    assert response.status_code == 202
    assert "sync_job_id" in response.data


@pytest.mark.django_db
def test_action_permissions(viewer_user, installation):
    viewer = APIClient()
    viewer.force_authenticate(user=viewer_user)
    assert viewer.post(f"{BASE}{installation.id}/activate/").status_code == 403
    assert viewer.post(f"{BASE}{installation.id}/revoke/").status_code == 403
    assert viewer.post(f"{BASE}{installation.id}/sync/").status_code == 403
    assert viewer.get(BASE).status_code == 403


@pytest.mark.django_db
def test_installation_filters(client_admin, org, installation):
    ConnectorInstallationService().activate(installation)
    other = ConnectorInstallation.objects.create(
        organization=org,
        connector_type="api-v2-fake",
        name="Other Pending",
        status=ConnectorInstallationStatus.PENDING,
        config={"account_id": "acc-2"},
    )
    ConnectorSyncJob.objects.create(
        installation=installation,
        status=ConnectorSyncJobStatus.FAILED,
        finished_at=timezone.now(),
        error="sync boom",
    )

    by_status = client_admin.get(BASE, {"status": "pending"})
    assert by_status.status_code == 200
    ids = {row["id"] for row in by_status.data["results"]}
    assert str(other.id) in ids
    assert str(installation.id) not in ids

    by_type = client_admin.get(BASE, {"connector_type": "api-v2-fake"})
    assert by_type.status_code == 200
    assert len(by_type.data["results"]) == 2

    by_health = client_admin.get(BASE, {"health": "degraded"})
    assert by_health.status_code == 200
    health_ids = {row["id"] for row in by_health.data["results"]}
    assert str(installation.id) in health_ids
    assert str(other.id) not in health_ids

    pending_health = client_admin.get(BASE, {"health": "pending"})
    assert {row["id"] for row in pending_health.data["results"]} == {str(other.id)}

    future = (timezone.now() + timedelta(days=1)).isoformat().replace("+00:00", "Z")
    by_created = client_admin.get(BASE, {"created_at__lte": future})
    assert by_created.status_code == 200
    assert len(by_created.data["results"]) == 2

    past = (timezone.now() - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    empty = client_admin.get(BASE, {"created_at__gte": future})
    assert empty.status_code == 200
    assert empty.data["results"] == []
    older = client_admin.get(BASE, {"created_at__lte": past})
    assert older.status_code == 200
    assert older.data["results"] == []


@pytest.mark.django_db
def test_org_ownership_on_actions(org, client_admin):
    other = Organization.objects.create(name="Other Org", slug="other-org-v2")
    foreign = ConnectorInstallation.objects.create(
        organization=other,
        connector_type="api-v2-fake",
        name="Foreign",
        status=ConnectorInstallationStatus.PENDING,
        config={"account_id": "x"},
    )
    assert client_admin.post(f"{BASE}{foreign.id}/activate/").status_code == 404
    assert client_admin.post(f"{BASE}{foreign.id}/revoke/").status_code == 404
    assert client_admin.get(f"{BASE}{foreign.id}/").status_code == 404


@pytest.mark.django_db
def test_list_never_leaks_credentials(client_admin, installation):
    ConnectorInstallationService().store_credentials(
        installation,
        access_token="oauth-access-plain",
        refresh_token="oauth-refresh-plain",
    )
    assert ConnectorCredential.objects.filter(
        connector_installation=installation
    ).exists()
    response = client_admin.get(BASE)
    assert response.status_code == 200
    blob = str(response.data)
    assert "oauth-access-plain" not in blob
    assert "oauth-refresh-plain" not in blob
    assert "super-secret-token" not in blob
    for row in response.data["results"]:
        assert "config" not in row
        assert set(row.keys()) == {
            "id",
            "connector_type",
            "name",
            "status",
            "auth_status",
            "health",
            "last_sync",
            "created_at",
            "updated_at",
        }
