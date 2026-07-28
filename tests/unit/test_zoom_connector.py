from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
import responses
from django.utils import timezone

from turing.connectors import ConnectorConfigurationError, ConnectorRegistry
from turing.connectors.builtins import register_builtin_connectors
from turing.connectors.exceptions import (
    AuthenticationError,
    ConnectorHealthError,
    ConnectorSyncError,
)
from turing.connectors.zoom.client import ZoomClient
from turing.connectors.zoom.connector import ZoomConnector
from turing.connectors.zoom.serializers import (
    normalize_meeting_recordings,
    normalize_recordings_list,
    pick_primary_recording,
)
from turing.domain.enums import ConnectorInstallationStatus, UseCase
from turing.domain.events import DomainEvent, EventName
from turing.events.bus import EventBus
from turing.models import ConnectorInstallation, ExternalReference, MediaAsset, Organization
from turing.services.connector_installation import ConnectorInstallationService
from turing.services.connector_sync import ConnectorSyncService


@pytest.fixture(autouse=True)
def _zoom_registry(settings):
    settings.TURING_ZOOM_CLIENT_ID = "test-client"
    settings.TURING_ZOOM_CLIENT_SECRET = "test-secret"
    settings.TURING_ZOOM_OAUTH_REDIRECT_URI = (
        "http://testserver/api/turing/v1/oauth/callback/zoom/"
    )
    ConnectorRegistry.clear()
    register_builtin_connectors()
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
        "connector_type": "zoom",
        "name": "Company Zoom",
        "status": ConnectorInstallationStatus.PENDING,
        "config": {},
    }
    store_tokens = kwargs.pop("store_tokens", True)
    defaults.update(kwargs)
    installation = ConnectorInstallation.objects.create(**defaults)
    if store_tokens:
        ConnectorInstallationService().store_credentials(
            installation,
            access_token="zoom-access-token",
            refresh_token="zoom-refresh-token",
            expires_at=timezone.now() + timedelta(hours=1),
        )
        ConnectorInstallationService().activate(installation)
        installation.refresh_from_db()
    return installation


def _meeting_payload() -> dict[str, Any]:
    return {
        "id": 111,
        "uuid": "mtg-uuid",
        "topic": "Weekly sync",
        "recording_files": [
            {
                "id": "rec-audio-1",
                "file_type": "M4A",
                "file_extension": "m4a",
                "file_size": 1000,
                "download_url": "https://zoom.example/rec-audio-1.m4a",
                "status": "completed",
                "recording_start": "2026-01-01T10:00:00Z",
                "recording_end": "2026-01-01T10:30:00Z",
                "recording_type": "audio_only",
            },
            {
                "id": "rec-chat-1",
                "file_type": "CHAT",
                "download_url": "https://zoom.example/chat.txt",
                "status": "completed",
            },
            {
                "id": "rec-video-1",
                "file_type": "MP4",
                "file_extension": "mp4",
                "download_url": "https://zoom.example/rec-video-1.mp4",
                "status": "completed",
            },
        ],
    }


@pytest.mark.django_db
def test_config_validation(org, settings):
    installation = _installation(org, store_tokens=False)
    settings.TURING_ZOOM_CLIENT_ID = ""
    settings.TURING_ZOOM_CLIENT_SECRET = ""
    with pytest.raises(ConnectorConfigurationError, match="TURING_ZOOM_CLIENT_ID"):
        ZoomConnector(installation).validate_config()

    settings.TURING_ZOOM_CLIENT_ID = "cid"
    settings.TURING_ZOOM_CLIENT_SECRET = "csecret"
    ZoomConnector(installation).validate_config()

    with pytest.raises(ConnectorConfigurationError, match="access token"):
        ZoomConnector(installation).validate_credentials()


def test_recording_normalization_and_primary():
    recordings = normalize_meeting_recordings(_meeting_payload())
    assert {r.recording_id for r in recordings} == {"rec-audio-1", "rec-video-1"}
    primary = pick_primary_recording(recordings)
    assert primary is not None
    assert primary.recording_id == "rec-audio-1"
    assert primary.file_type == "M4A"

    listed = normalize_recordings_list({"meetings": [_meeting_payload()]})
    assert len(listed) == 2


@responses.activate
def test_zoom_client_list_and_health():
    responses.add(
        responses.GET,
        "https://api.zoom.us/v2/users/me",
        json={"display_name": "Acme User", "id": "u1"},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.zoom.us/v2/users/me/recordings",
        json={"meetings": [_meeting_payload()]},
        status=200,
    )
    client = ZoomClient(api_token="secret")
    health = client.health_check()
    assert health["ok"] is True
    assert health["account_name"] == "Acme User"
    assert "secret" not in str(health)

    recordings = client.list_recordings()
    assert len(recordings) == 2
    assert any(r.recording_id == "rec-audio-1" for r in recordings)

    responses.add(
        responses.GET,
        "https://api.zoom.us/v2/meetings/111/recordings",
        json={"message": "Unauthorized"},
        status=401,
    )
    with pytest.raises(AuthenticationError, match="401"):
        client.fetch_recording_metadata("111")


@pytest.mark.django_db
def test_pull_media_uses_client(org):
    installation = _installation(org)
    client = MagicMock()
    client.list_recordings.return_value = normalize_meeting_recordings(_meeting_payload())
    connector = ZoomConnector(installation, client=client)
    items = connector.pull_media()
    assert len(items) == 1
    item = items[0]
    assert item.external_id == "rec-audio-1"
    assert item.source_url == "https://zoom.example/rec-audio-1.m4a"
    assert item.metadata["external_system"] == "zoom"
    assert item.metadata["external_type"] == "meeting"
    assert item.metadata["media_url"] == item.source_url
    client.list_recordings.assert_called_once()


@pytest.mark.django_db(transaction=True)
def test_sync_creates_media_and_external_ref(org, monkeypatch):
    installation = _installation(org)
    created_urls: list[str] = []

    real_create = __import__(
        "turing.services.media", fromlist=["MediaService"]
    ).MediaService.create_from_url

    def _create_from_url(self, **kwargs):
        created_urls.append(kwargs["url"])
        assert kwargs["organization"] == org
        assert kwargs["use_case"] == UseCase.MEETING
        assert "zoom-access-token" not in str(kwargs)
        return real_create(self, **kwargs)

    monkeypatch.setattr(
        "turing.services.media.MediaService.create_from_url",
        _create_from_url,
    )

    client = MagicMock()
    client.list_recordings.return_value = normalize_meeting_recordings(_meeting_payload())
    connector = ZoomConnector(installation, client=client)
    result = connector.sync()
    assert result.records_processed == 1
    assert created_urls == ["https://zoom.example/rec-audio-1.m4a"]

    asset = MediaAsset.objects.get(external_url="https://zoom.example/rec-audio-1.m4a")
    assert asset.organization_id == org.id
    assert asset.use_case == UseCase.MEETING
    ref = ExternalReference.objects.get(
        organization=org,
        external_system="zoom",
        external_type="meeting",
        external_id="rec-audio-1",
    )
    assert ref.media_id == asset.id

    result2 = connector.sync()
    assert result2.records_processed == 0
    assert result2.details["skipped"] == 1
    assert MediaAsset.objects.filter(organization=org).count() == 1


@pytest.mark.django_db(transaction=True)
def test_sync_success_via_service_emits_events(org, settings, monkeypatch):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    seen: list[DomainEvent] = []
    EventBus.subscribe("*", seen.append)

    installation = _installation(org)
    client = MagicMock()
    client.list_recordings.return_value = normalize_meeting_recordings(_meeting_payload())

    original_create = ConnectorRegistry.create

    def _create(inst):
        if inst.connector_type == "zoom":
            return ZoomConnector(inst, client=client)
        return original_create(inst)

    monkeypatch.setattr(ConnectorRegistry, "create", staticmethod(_create))

    job = ConnectorSyncService().start_sync(installation, auto_enqueue=True)
    job.refresh_from_db()
    assert job.status == "completed"
    assert job.records_processed == 1
    names = [e.name for e in seen]
    assert EventName.CONNECTOR_SYNC_STARTED in names
    assert EventName.CONNECTOR_SYNC_COMPLETED in names
    assert EventName.MEDIA_CREATED in names
    assert "zoom-access-token" not in str([e.payload for e in seen])


@pytest.mark.django_db
def test_sync_failure_when_media_create_fails(org, monkeypatch):
    installation = _installation(org)
    client = MagicMock()
    client.list_recordings.return_value = normalize_meeting_recordings(_meeting_payload())

    def _boom(*args, **kwargs):
        raise RuntimeError("storage down")

    monkeypatch.setattr("turing.services.media.MediaService.create_from_url", _boom)
    connector = ZoomConnector(installation, client=client)
    with pytest.raises(ConnectorSyncError, match="storage down"):
        connector.sync()


@pytest.mark.django_db
def test_registry_includes_zoom():
    assert "zoom" in ConnectorRegistry.types()
    assert ConnectorRegistry.get("zoom") is ZoomConnector
    assert ConnectorRegistry.get("zoom").auth_type == "oauth2"
