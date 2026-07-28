from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from turing.connectors import (
    BaseConnector,
    ConnectorConfigurationError,
    ConnectorRegistry,
    ConnectorSyncResult,
    MediaPullItem,
)
from turing.domain.enums import ConnectorInstallationStatus, ConnectorSyncJobStatus, TuringRole
from turing.models import (
    ConnectorInstallation,
    ConnectorSyncJob,
    Organization,
    TuringMembership,
)

User = get_user_model()


class ApiFakeConnector(BaseConnector):
    connector_type = "api-fake"
    display_name = "API Fake"

    def validate_config(self) -> None:
        if not self.config.get("account_id"):
            raise ConnectorConfigurationError("account_id is required.")

    def health_check(self) -> dict[str, Any]:
        return {"ok": True}

    def pull_media(self, **kwargs: Any) -> list[MediaPullItem]:
        return [MediaPullItem(external_id="1")]

    def sync(self) -> ConnectorSyncResult:
        self.validate_config()
        return ConnectorSyncResult(records_processed=1)


def _membership(user, org, role):
    return TuringMembership.objects.create(
        user=user, organization=org, role=role, is_active=True
    )


@pytest.fixture(autouse=True)
def _register_fake():
    from turing.connectors.builtins import register_builtin_connectors

    ConnectorRegistry.clear()
    ConnectorRegistry.register(ApiFakeConnector)
    yield
    ConnectorRegistry.clear()
    register_builtin_connectors()


@pytest.fixture
def org(db):
    return Organization.get_default()


@pytest.fixture
def admin_user(org):
    user = User.objects.create_user(username="conn-admin", password="pass")
    _membership(user, org, TuringRole.ADMIN)
    return user


@pytest.fixture
def viewer_user(org):
    user = User.objects.create_user(username="conn-viewer", password="pass")
    _membership(user, org, TuringRole.VIEWER)
    return user


@pytest.fixture
def client_admin(admin_user):
    client = APIClient()
    client.force_authenticate(user=admin_user)
    return client


@pytest.mark.django_db
def test_connector_catalog_listing(client_admin):
    response = client_admin.get("/api/turing/v1/connectors/")
    assert response.status_code == 200
    assert response.data == [
        {
            "connector_type": "api-fake",
            "display_name": "API Fake",
            "provider": "",
            "description": "",
            "category": "other",
            "documentation_url": "",
            "icon_url": "",
            "auth_type": "api_key",
            "capabilities": {
                "oauth": False,
                "refresh": False,
                "revoke": False,
            },
            "supported_sync_types": ["media"],
            "required_scopes": [],
            "installation_requirements": {
                "oauth_scopes": [],
                "config_fields": [],
                "messages": ["api_credentials"],
            },
        }
    ]


@pytest.mark.django_db
def test_installation_crud_hides_config(client_admin):
    create = client_admin.post(
        "/api/turing/v1/connector-installations/",
        {
            "connector_type": "api-fake",
            "name": "Company Fake",
            "config": {"account_id": "acc-1", "api_token": "secret-value"},
        },
        format="json",
    )
    assert create.status_code == 201
    assert create.data["connector_type"] == "api-fake"
    assert create.data["name"] == "Company Fake"
    assert "config" not in create.data
    assert "secret-value" not in str(create.data)
    inst_id = create.data["id"]

    stored = ConnectorInstallation.objects.get(pk=inst_id)
    assert stored.config["api_token"] == "secret-value"

    detail = client_admin.get(f"/api/turing/v1/connector-installations/{inst_id}/")
    assert detail.status_code == 200
    assert "config" not in detail.data
    assert "auth_status" in detail.data
    assert detail.data["auth_status"]["auth_type"] == "api_key"
    assert detail.data["auth_status"]["has_credentials"] is True

    patched = client_admin.patch(
        f"/api/turing/v1/connector-installations/{inst_id}/",
        {"status": ConnectorInstallationStatus.REVOKED, "name": "Paused"},
        format="json",
    )
    assert patched.status_code == 200
    assert patched.data["status"] == ConnectorInstallationStatus.REVOKED
    assert patched.data["name"] == "Paused"
    assert "config" not in patched.data

    deleted = client_admin.delete(f"/api/turing/v1/connector-installations/{inst_id}/")
    assert deleted.status_code == 204
    assert not ConnectorInstallation.objects.filter(pk=inst_id).exists()


@pytest.mark.django_db
def test_installation_config_validation(client_admin):
    missing = client_admin.post(
        "/api/turing/v1/connector-installations/",
        {
            "connector_type": "api-fake",
            "name": "Bad",
            "config": {},
        },
        format="json",
    )
    assert missing.status_code == 400
    assert "account_id" in str(missing.data).lower()

    unknown = client_admin.post(
        "/api/turing/v1/connector-installations/",
        {
            "connector_type": "zoom",
            "name": "Zoom",
            "config": {"account_id": "x"},
        },
        format="json",
    )
    assert unknown.status_code == 400


@pytest.mark.django_db
def test_installation_permission_checks(viewer_user, admin_user):
    viewer = APIClient()
    viewer.force_authenticate(user=viewer_user)
    assert viewer.get("/api/turing/v1/connectors/").status_code == 403
    assert viewer.get("/api/turing/v1/connector-installations/").status_code == 403

    admin = APIClient()
    admin.force_authenticate(user=admin_user)
    assert admin.get("/api/turing/v1/connectors/").status_code == 200
    assert admin.get("/api/turing/v1/connector-installations/").status_code == 200


@pytest.mark.django_db(transaction=True)
def test_sync_enqueue_and_job_status(client_admin, settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    created = client_admin.post(
        "/api/turing/v1/connector-installations/",
        {
            "connector_type": "api-fake",
            "name": "Sync me",
            "config": {"account_id": "acc"},
        },
        format="json",
    )
    inst_id = created.data["id"]

    sync = client_admin.post(f"/api/turing/v1/connector-installations/{inst_id}/sync/")
    assert sync.status_code == 202
    assert "sync_job_id" in sync.data
    job_id = sync.data["sync_job_id"]

    job = ConnectorSyncJob.objects.get(pk=job_id)
    assert job.status == ConnectorSyncJobStatus.COMPLETED
    assert job.records_processed == 1

    status_resp = client_admin.get(f"/api/turing/v1/connector-sync-jobs/{job_id}/")
    assert status_resp.status_code == 200
    assert status_resp.data["status"] == ConnectorSyncJobStatus.COMPLETED
    assert status_resp.data["records_processed"] == 1
    assert status_resp.data["installation"] == inst_id or str(
        status_resp.data["installation"]
    ) == str(inst_id)


@pytest.mark.django_db
def test_connector_org_isolation(org, admin_user):
    other = Organization.objects.create(name="Other", slug="conn-api-other")
    outsider = User.objects.create_user(username="conn-out", password="pass")
    _membership(outsider, other, TuringRole.ADMIN)

    client = APIClient()
    client.force_authenticate(user=admin_user)
    created = client.post(
        "/api/turing/v1/connector-installations/",
        {
            "connector_type": "api-fake",
            "name": "Mine",
            "config": {"account_id": "a"},
        },
        format="json",
    )
    inst_id = created.data["id"]

    other_client = APIClient()
    other_client.force_authenticate(user=outsider)
    listed = other_client.get("/api/turing/v1/connector-installations/")
    rows = listed.data["results"] if "results" in listed.data else listed.data
    assert all(row["id"] != inst_id for row in rows)
    assert other_client.get(f"/api/turing/v1/connector-installations/{inst_id}/").status_code == 404
