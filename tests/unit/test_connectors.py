from __future__ import annotations

from typing import Any

import pytest
from django.db import IntegrityError

from turing.connectors import (
    BaseConnector,
    ConnectorConfigurationError,
    ConnectorNotFoundError,
    ConnectorRegistry,
    ConnectorSyncResult,
    MediaPullItem,
)
from turing.domain.enums import (
    ConnectorInstallationStatus,
    ConnectorSyncJobStatus,
)
from turing.domain.events import DomainEvent, EventName
from turing.events.bus import EventBus
from turing.models import ConnectorInstallation, ConnectorSyncJob, Organization
from turing.services.connector_sync import ConnectorSyncService
from turing.domain.exceptions import ValidationError


class FakeConnector(BaseConnector):
    connector_type = "fake"
    display_name = "Fake Connector"

    def validate_config(self) -> None:
        if not self.config.get("account_id"):
            raise ConnectorConfigurationError("account_id is required.")

    def health_check(self) -> dict[str, Any]:
        return {"ok": True, "account_id": self.config.get("account_id")}

    def pull_media(self, **kwargs: Any) -> list[MediaPullItem]:
        return [
            MediaPullItem(
                external_id="rec-1",
                source_url="https://example.com/rec-1.mp3",
                filename="rec-1.mp3",
            )
        ]

    def sync(self) -> ConnectorSyncResult:
        self.validate_config()
        items = self.pull_media()
        return ConnectorSyncResult(records_processed=len(items), media_items=items)


class BrokenConnector(BaseConnector):
    connector_type = "broken"
    display_name = "Broken"

    def validate_config(self) -> None:
        return None

    def health_check(self) -> dict[str, Any]:
        return {"ok": False}

    def pull_media(self, **kwargs: Any) -> list[MediaPullItem]:
        return []

    def sync(self) -> ConnectorSyncResult:
        raise ConnectorConfigurationError("remote refused")


@pytest.fixture(autouse=True)
def _registry():
    from turing.connectors.builtins import register_builtin_connectors

    ConnectorRegistry.clear()
    ConnectorRegistry.register(FakeConnector)
    ConnectorRegistry.register(BrokenConnector)
    EventBus.clear()
    yield
    ConnectorRegistry.clear()
    register_builtin_connectors()
    EventBus.clear()


@pytest.fixture
def org(db):
    return Organization.get_default()


@pytest.fixture
def other_org(db):
    return Organization.objects.create(name="Other", slug="connector-other")


def _installation(org, **kwargs) -> ConnectorInstallation:
    defaults = {
        "organization": org,
        "connector_type": "fake",
        "name": "Fake install",
        "status": ConnectorInstallationStatus.ACTIVE,
        "config": {"account_id": "acc-1", "api_token": "super-secret"},
    }
    defaults.update(kwargs)
    return ConnectorInstallation.objects.create(**defaults)


@pytest.mark.django_db
def test_registry_resolve_and_list():
    assert ConnectorRegistry.types() == ["broken", "fake"]
    assert ConnectorRegistry.get("fake") is FakeConnector
    catalog = ConnectorRegistry.list_available()
    assert {"connector_type": "fake", "display_name": "Fake Connector"} in catalog
    with pytest.raises(ConnectorNotFoundError):
        ConnectorRegistry.get("zoom")


@pytest.mark.django_db
def test_connector_resolution_from_installation(org):
    installation = _installation(org)
    connector = ConnectorRegistry.create(installation)
    assert isinstance(connector, FakeConnector)
    assert connector.name == "Fake Connector"
    assert connector.health_check()["ok"] is True
    assert installation.public_config()["api_token"] == "********"
    assert installation.public_config()["account_id"] == "acc-1"


@pytest.mark.django_db
def test_org_isolation_unique_name(org, other_org):
    _installation(org, name="Shared name")
    _installation(other_org, name="Shared name")
    with pytest.raises(IntegrityError):
        _installation(org, name="Shared name", connector_type="broken")


@pytest.mark.django_db(transaction=True)
def test_sync_lifecycle_success(org, settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    seen: list[DomainEvent] = []
    EventBus.subscribe("*", seen.append)

    installation = _installation(org)
    job = ConnectorSyncService().start_sync(installation, auto_enqueue=True)
    job.refresh_from_db()
    assert job.status == ConnectorSyncJobStatus.COMPLETED
    assert job.records_processed == 1
    assert job.started_at is not None
    assert job.finished_at is not None

    names = [e.name for e in seen]
    assert EventName.CONNECTOR_SYNC_STARTED in names
    assert EventName.CONNECTOR_SYNC_COMPLETED in names
    completed = next(e for e in seen if e.name == EventName.CONNECTOR_SYNC_COMPLETED)
    assert completed.payload["installation_id"] == str(installation.id)
    assert completed.payload["organization_id"] == org.id
    assert completed.payload["records_processed"] == 1
    assert "super-secret" not in str(completed.payload)


@pytest.mark.django_db(transaction=True)
def test_sync_lifecycle_failure(org, settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    seen: list[DomainEvent] = []
    EventBus.subscribe("*", seen.append)

    installation = _installation(org, connector_type="broken", name="Broken install")
    job = ConnectorSyncService().start_sync(installation, auto_enqueue=True)
    job.refresh_from_db()
    installation.refresh_from_db()
    assert job.status == ConnectorSyncJobStatus.FAILED
    assert "remote refused" in job.error
    assert installation.status == ConnectorInstallationStatus.ERROR
    assert EventName.CONNECTOR_SYNC_FAILED in [e.name for e in seen]
    failed = next(e for e in seen if e.name == EventName.CONNECTOR_SYNC_FAILED)
    assert failed.payload["error_code"] == "connector_sync_failed"
    assert "remote refused" not in str(failed.payload)


@pytest.mark.django_db
def test_disabled_installation_cannot_sync(org):
    installation = _installation(org, status=ConnectorInstallationStatus.DISABLED)
    with pytest.raises(ValidationError):
        ConnectorSyncService().start_sync(installation, auto_enqueue=False)


@pytest.mark.django_db(transaction=True)
def test_manual_run_sync_without_enqueue(org):
    installation = _installation(org)
    job = ConnectorSyncService().start_sync(installation, auto_enqueue=False)
    assert job.status == ConnectorSyncJobStatus.PENDING
    assert ConnectorSyncJob.objects.filter(pk=job.pk).exists()
    finished = ConnectorSyncService().run_sync(str(job.id))
    assert finished.status == ConnectorSyncJobStatus.COMPLETED
