from __future__ import annotations

from typing import Any

import pytest

from turing.celery_schedule import build_celery_beat_schedule
from turing.connectors import BaseConnector, ConnectorRegistry, ConnectorSyncResult
from turing.domain.enums import (
    ConnectorInstallationStatus,
    ConnectorSyncJobStatus,
)
from turing.events.bus import EventBus
from turing.models import ConnectorInstallation, ConnectorSyncJob, Organization
from turing.services.connector_sync import ConnectorSyncService
from turing.tasks.connectors import schedule_connector_syncs


class SchedFakeConnector(BaseConnector):
    connector_type = "sched_fake"
    display_name = "Sched Fake"

    def validate_config(self) -> None:
        return None

    def health_check(self) -> dict[str, Any]:
        return {"ok": True}

    def pull_media(self, **kwargs: Any) -> list:
        return []

    def sync(self) -> ConnectorSyncResult:
        return ConnectorSyncResult(records_processed=1, media_items=[])


@pytest.fixture(autouse=True)
def _registry():
    from turing.connectors.builtins import register_builtin_connectors

    ConnectorRegistry.clear()
    ConnectorRegistry.register(SchedFakeConnector)
    EventBus.clear()
    yield
    ConnectorRegistry.clear()
    register_builtin_connectors()
    EventBus.clear()


@pytest.fixture
def org(db):
    return Organization.get_default()


def _installation(org, **kwargs) -> ConnectorInstallation:
    defaults = {
        "organization": org,
        "connector_type": "sched_fake",
        "name": "Sched install",
        "status": ConnectorInstallationStatus.ACTIVE,
        "config": {"token": "secret-value"},
    }
    defaults.update(kwargs)
    return ConnectorInstallation.objects.create(**defaults)


def test_beat_schedule_registers_connector_sync(settings):
    settings.TURING_CONNECTOR_SYNC_ENABLED = True
    settings.TURING_CONNECTOR_SYNC_INTERVAL_SECONDS = 7200
    settings.TURING_OUTBOX_DISPATCH_ENABLED = False
    schedule = build_celery_beat_schedule()
    assert "turing-schedule-connector-syncs" in schedule
    entry = schedule["turing-schedule-connector-syncs"]
    assert entry["task"] == "turing.tasks.connectors.schedule_connector_syncs"
    assert entry["schedule"] == 7200.0
    assert "turing-dispatch-outbox-events" not in schedule


def test_beat_schedule_omits_connector_sync_when_disabled(settings):
    settings.TURING_CONNECTOR_SYNC_ENABLED = False
    settings.TURING_OUTBOX_DISPATCH_ENABLED = True
    schedule = build_celery_beat_schedule()
    assert "turing-schedule-connector-syncs" not in schedule
    assert "turing-dispatch-outbox-events" in schedule


@pytest.mark.django_db
def test_discover_active_installations_only(org):
    active = _installation(org, name="active")
    _installation(
        org,
        name="disabled",
        status=ConnectorInstallationStatus.DISABLED,
    )
    error = _installation(
        org,
        name="error",
        status=ConnectorInstallationStatus.ERROR,
    )
    inactive_org = Organization.objects.create(
        name="Inactive Org",
        slug="inactive-connector-org",
        is_active=False,
    )
    _installation(inactive_org, name="on-inactive-org")

    found = list(ConnectorSyncService().discover_schedulable_installations())
    ids = {i.id for i in found}
    assert active.id in ids
    assert error.id in ids
    assert len(found) == 2


@pytest.mark.django_db
def test_skips_duplicate_running_sync(org):
    installation = _installation(org)
    ConnectorSyncJob.objects.create(
        installation=installation,
        status=ConnectorSyncJobStatus.RUNNING,
    )
    service = ConnectorSyncService()
    assert service.has_in_flight_sync(installation) is True
    assert service.start_sync_if_idle(installation, auto_enqueue=False) is None
    assert ConnectorSyncJob.objects.filter(installation=installation).count() == 1

    counts = service.schedule_due_installations()
    assert counts["examined"] >= 1
    assert counts["started"] == 0
    assert counts["skipped_in_flight"] >= 1
    assert ConnectorSyncJob.objects.filter(installation=installation).count() == 1


@pytest.mark.django_db
def test_failed_sync_does_not_block_future_schedule(org, settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    installation = _installation(org, status=ConnectorInstallationStatus.ERROR)
    ConnectorSyncJob.objects.create(
        installation=installation,
        status=ConnectorSyncJobStatus.FAILED,
        error="previous failure",
    )
    service = ConnectorSyncService()
    assert service.has_in_flight_sync(installation) is False

    job = service.start_sync_if_idle(installation, auto_enqueue=False)
    assert job is not None
    assert job.status == ConnectorSyncJobStatus.PENDING
    assert (
        ConnectorSyncJob.objects.filter(installation=installation).count() == 2
    )


@pytest.mark.django_db
def test_schedule_connector_syncs_task(org, settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    _installation(org)
    result = schedule_connector_syncs.delay()
    counts = result.get()
    assert counts["examined"] == 1
    assert counts["started"] == 1
    assert counts["skipped_in_flight"] == 0
    assert ConnectorSyncJob.objects.count() == 1
