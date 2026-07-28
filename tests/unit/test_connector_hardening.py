from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient

from turing.connectors import (
    AuthenticationError,
    BaseConnector,
    ConnectorRegistry,
    ConnectorSyncResult,
    PermanentConnectorError,
    TemporaryConnectorError,
)
from turing.domain.enums import (
    ConnectorInstallationStatus,
    ConnectorSyncJobStatus,
    TuringRole,
)
from turing.models import (
    ConnectorCredential,
    ConnectorInstallation,
    ConnectorSyncJob,
    Organization,
    TuringMembership,
)
from turing.services.connector_installation import ConnectorInstallationService
from turing.services.connector_sync import ConnectorSyncService
from turing.services.oauth_state import OAuthStateService

User = get_user_model()


class CapFakeConnector(BaseConnector):
    connector_type = "cap-fake"
    display_name = "Capability Fake"
    supports_oauth = False
    supports_refresh = False
    supports_revoke = False
    supported_sync_types = ("media", "metadata")

    def validate_config(self) -> None:
        return None

    def health_check(self) -> dict[str, Any]:
        return {"ok": True}

    def pull_media(self, **kwargs: Any) -> list:
        return []

    def sync(self) -> ConnectorSyncResult:
        return ConnectorSyncResult(records_processed=0)


class TempFailConnector(BaseConnector):
    connector_type = "temp-fail"
    display_name = "Temp Fail"

    def validate_config(self) -> None:
        return None

    def health_check(self) -> dict[str, Any]:
        return {"ok": True}

    def pull_media(self, **kwargs: Any) -> list:
        return []

    def sync(self) -> ConnectorSyncResult:
        raise TemporaryConnectorError("remote blip")


class AuthFailConnector(BaseConnector):
    connector_type = "auth-fail"
    display_name = "Auth Fail"
    auth_type = "oauth2"
    supports_oauth = True

    def validate_config(self) -> None:
        return None

    def validate_credentials(self) -> None:
        return None

    def health_check(self) -> dict[str, Any]:
        return {"ok": False}

    def pull_media(self, **kwargs: Any) -> list:
        return []

    def sync(self) -> ConnectorSyncResult:
        raise AuthenticationError("token rejected")


class PermanentFailConnector(BaseConnector):
    connector_type = "perm-fail"
    display_name = "Perm Fail"

    def validate_config(self) -> None:
        return None

    def health_check(self) -> dict[str, Any]:
        return {"ok": True}

    def pull_media(self, **kwargs: Any) -> list:
        return []

    def sync(self) -> ConnectorSyncResult:
        raise PermanentConnectorError("bad remote config")


@pytest.fixture(autouse=True)
def _registry():
    from turing.connectors.builtins import register_builtin_connectors

    ConnectorRegistry.clear()
    ConnectorRegistry.register(CapFakeConnector)
    ConnectorRegistry.register(TempFailConnector)
    ConnectorRegistry.register(AuthFailConnector)
    ConnectorRegistry.register(PermanentFailConnector)
    cache.clear()
    yield
    ConnectorRegistry.clear()
    register_builtin_connectors()
    cache.clear()


@pytest.fixture
def org(db):
    return Organization.get_default()


@pytest.fixture
def admin_client(org):
    user = User.objects.create_user(username="hard-admin", password="pass")
    TuringMembership.objects.create(
        user=user, organization=org, role=TuringRole.ADMIN, is_active=True
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _installation(org, connector_type="cap-fake", **kwargs) -> ConnectorInstallation:
    defaults = {
        "organization": org,
        "connector_type": connector_type,
        "name": f"{connector_type}-install",
        "status": ConnectorInstallationStatus.ACTIVE,
        "config": {},
    }
    defaults.update(kwargs)
    return ConnectorInstallation.objects.create(**defaults)


@pytest.mark.django_db
def test_capability_api_exposes_metadata(admin_client):
    response = admin_client.get("/api/turing/v1/connectors/")
    assert response.status_code == 200
    by_type = {row["type"]: row for row in response.data}
    assert "cap-fake" in by_type
    row = by_type["cap-fake"]
    assert row["supports_oauth"] is False
    assert row["supports_refresh"] is False
    assert row["supports_revoke"] is False
    assert row["supported_sync_types"] == ["media", "metadata"]
    assert "secret" not in str(response.data).lower()


@pytest.mark.django_db
def test_oauth_state_replay_rejected(org):
    service = OAuthStateService()
    installation = _installation(org, connector_type="auth-fail", name="oauth-state")
    state = service.generate(
        installation_id=str(installation.id),
        organization_id=org.id,
        connector_type="auth-fail",
    )
    claims = service.validate(state, expected_connector_type="auth-fail")
    assert claims.installation_id == str(installation.id)
    assert claims.connector_type == "auth-fail"

    from turing.connectors.exceptions import ConnectorConfigurationError

    with pytest.raises(ConnectorConfigurationError, match="already been used"):
        service.validate(state, expected_connector_type="auth-fail")


@pytest.mark.django_db
def test_credential_lifecycle_timestamps(org):
    installation = _installation(org, name="cred-life")
    service = ConnectorInstallationService()
    before = timezone.now()
    cred = service.store_credentials(
        installation,
        access_token="tok-1",
        refresh_token="ref-1",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    cred.refresh_from_db()
    assert cred.last_refreshed_at is not None
    assert cred.last_refreshed_at >= before
    assert cred.revoked_at is None

    service.store_credentials(installation, access_token="tok-2", refresh_token="ref-2")
    cred.refresh_from_db()
    first_refresh = cred.last_refreshed_at

    service.revoke(installation)
    cred.refresh_from_db()
    assert cred.revoked_at is not None
    assert cred.encrypted_access_token == ""
    assert first_refresh is not None


@pytest.mark.django_db
def test_sync_health_helpers(org):
    installation = _installation(org, name="health-inst")
    assert installation.current_health() == "healthy"
    assert installation.health_summary()["last_successful_sync_at"] is None

    ConnectorSyncJob.objects.create(
        installation=installation,
        status=ConnectorSyncJobStatus.COMPLETED,
        finished_at=timezone.now(),
        records_processed=1,
    )
    assert installation.current_health() == "healthy"
    assert installation.last_successful_sync() is not None

    ConnectorSyncJob.objects.create(
        installation=installation,
        status=ConnectorSyncJobStatus.FAILED,
        finished_at=timezone.now() + timedelta(seconds=1),
        error="boom",
    )
    assert installation.current_health() == "degraded"
    assert "boom" in installation.health_summary()["last_failed_sync_error"]

    installation.status = ConnectorInstallationStatus.ERROR
    installation.save(update_fields=["status"])
    assert installation.current_health() == "unhealthy"


@pytest.mark.django_db
def test_temporary_error_marks_retryable(org):
    installation = _installation(org, connector_type="temp-fail", name="temp")
    job = ConnectorSyncService().start_sync(installation, auto_enqueue=False)
    with pytest.raises(TemporaryConnectorError):
        ConnectorSyncService().run_sync(str(job.id))
    job.refresh_from_db()
    assert job.status == ConnectorSyncJobStatus.PENDING
    assert "blip" in job.error
    installation.refresh_from_db()
    assert installation.status == ConnectorInstallationStatus.ACTIVE


@pytest.mark.django_db
def test_authentication_error_expires_installation(org):
    installation = _installation(org, connector_type="auth-fail", name="auth")
    ConnectorInstallationService().store_credentials(
        installation, access_token="x", refresh_token="y"
    )
    job = ConnectorSyncService().start_sync(installation, auto_enqueue=False)
    finished = ConnectorSyncService().run_sync(str(job.id))
    finished.refresh_from_db()
    installation.refresh_from_db()
    assert finished.status == ConnectorSyncJobStatus.FAILED
    assert installation.status == ConnectorInstallationStatus.EXPIRED


@pytest.mark.django_db
def test_permanent_error_fails_job(org):
    installation = _installation(org, connector_type="perm-fail", name="perm")
    job = ConnectorSyncService().start_sync(installation, auto_enqueue=False)
    finished = ConnectorSyncService().run_sync(str(job.id))
    finished.refresh_from_db()
    installation.refresh_from_db()
    assert finished.status == ConnectorSyncJobStatus.FAILED
    assert installation.status == ConnectorInstallationStatus.ERROR
