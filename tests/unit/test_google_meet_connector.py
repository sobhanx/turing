from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

import pytest
import responses
from django.utils import timezone
from rest_framework.test import APIClient

from turing.connectors import ConnectorConfigurationError, ConnectorRegistry
from turing.connectors.builtins import register_builtin_connectors
from turing.connectors.exceptions import AuthenticationError, ConnectorSyncError
from turing.connectors.google_meet.client import GoogleMeetClient
from turing.connectors.google_meet.connector import GoogleMeetConnector
from turing.connectors.google_meet.oauth import GoogleMeetOAuthClient
from turing.connectors.google_meet.serializers import (
    normalize_meeting_recordings,
    normalize_recordings_list,
    pick_primary_recording,
)
from turing.domain.enums import ConnectorInstallationStatus, UseCase
from turing.domain.events import DomainEvent, EventName
from turing.events.bus import EventBus
from turing.models import (
    ConnectorCredential,
    ConnectorInstallation,
    ExternalReference,
    MediaAsset,
    Organization,
)
from turing.security.secrets import is_encrypted
from turing.services.connector_installation import ConnectorInstallationService
from turing.services.connector_sync import ConnectorSyncService
from turing.services.oauth_state import OAuthStateService, parse_oauth_state

TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
REDIRECT = "http://testserver/api/turing/v1/oauth/callback/google_meet/"
DRIVE = "https://www.googleapis.com/drive/v3/"
USERINFO = "https://www.googleapis.com/oauth2/v3/userinfo"


@pytest.fixture(autouse=True)
def _gm_registry(settings):
    settings.TURING_GOOGLE_MEET_CLIENT_ID = "gm-client-id"
    settings.TURING_GOOGLE_MEET_CLIENT_SECRET = "gm-client-secret"
    settings.TURING_GOOGLE_MEET_OAUTH_REDIRECT_URI = REDIRECT
    settings.TURING_GOOGLE_MEET_OAUTH_TOKEN_URL = TOKEN_URL
    settings.TURING_GOOGLE_MEET_OAUTH_REVOKE_URL = REVOKE_URL
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


def _pending_installation(org, **kwargs) -> ConnectorInstallation:
    defaults = {
        "organization": org,
        "connector_type": "google_meet",
        "name": "Company Meet",
        "status": ConnectorInstallationStatus.PENDING,
        "config": {},
    }
    defaults.update(kwargs)
    return ConnectorInstallation.objects.create(**defaults)


def _authorized_installation(org, **kwargs) -> ConnectorInstallation:
    installation = _pending_installation(org, **kwargs)
    ConnectorInstallationService().store_credentials(
        installation,
        access_token="gm-access-token",
        refresh_token="gm-refresh-token",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    ConnectorInstallationService().activate(installation)
    installation.refresh_from_db()
    return installation


def _files_payload() -> dict[str, Any]:
    return {
        "files": [
            {
                "id": "rec-audio-1",
                "name": "Meet Recording audio.m4a",
                "mimeType": "audio/mp4",
                "size": "1000",
                "createdTime": "2026-01-01T10:00:00Z",
                "webContentLink": "https://drive.example/rec-audio-1.m4a",
                "appProperties": {"meetingId": "mtg-1"},
            },
            {
                "id": "rec-video-1",
                "name": "Meet Recording video.mp4",
                "mimeType": "video/mp4",
                "size": "5000",
                "createdTime": "2026-01-01T10:00:00Z",
                "webContentLink": "https://drive.example/rec-video-1.mp4",
                "appProperties": {"meetingId": "mtg-1"},
            },
        ]
    }


@pytest.mark.django_db
def test_authorization_url(org):
    installation = _pending_installation(org)
    url = GoogleMeetConnector(installation).authorization_url()
    parsed = urlparse(url)
    assert "accounts.google.com" in parsed.netloc
    qs = parse_qs(parsed.query)
    assert qs["client_id"] == ["gm-client-id"]
    assert qs["access_type"] == ["offline"]
    assert qs["redirect_uri"] == [REDIRECT]
    inst_id, org_id = parse_oauth_state(qs["state"][0], consume=False)
    assert inst_id == str(installation.id)
    assert org_id == str(org.id)


@responses.activate
@pytest.mark.django_db
def test_oauth_callback_exchange(org):
    installation = _pending_installation(org)
    state = OAuthStateService().generate(
        installation_id=str(installation.id),
        organization_id=org.id,
        connector_type="google_meet",
    )
    responses.add(
        responses.POST,
        TOKEN_URL,
        json={
            "access_token": "google-access-plain",
            "refresh_token": "google-refresh-plain",
            "expires_in": 3600,
            "token_type": "Bearer",
        },
        status=200,
    )
    client = APIClient()
    response = client.get(
        "/api/turing/v1/oauth/callback/google_meet/",
        {"code": "auth-code", "state": state},
    )
    assert response.status_code == 200
    assert "google-access-plain" not in str(response.data)
    assert response.data["status"] == ConnectorInstallationStatus.ACTIVE
    cred = ConnectorCredential.objects.get(connector_installation=installation)
    assert is_encrypted(cred.encrypted_access_token)
    assert (
        GoogleMeetConnector(installation)._decrypt_access_token()
        == "google-access-plain"
    )


@responses.activate
@pytest.mark.django_db
def test_token_refresh(org):
    installation = _authorized_installation(org)
    responses.add(
        responses.POST,
        TOKEN_URL,
        json={
            "access_token": "google-access-new",
            "expires_in": 3600,
        },
        status=200,
    )
    cred = ConnectorCredential.objects.get(connector_installation=installation)
    cred.expires_at = timezone.now() - timedelta(minutes=1)
    cred.save(update_fields=["expires_at"])
    connector = GoogleMeetConnector(installation)
    connector.ensure_fresh_credentials()
    assert connector._decrypt_access_token() == "google-access-new"


@responses.activate
@pytest.mark.django_db
def test_revoke(org):
    installation = _authorized_installation(org)
    responses.add(responses.POST, REVOKE_URL, status=200)
    ConnectorInstallationService().revoke(installation)
    installation.refresh_from_db()
    assert installation.status == ConnectorInstallationStatus.REVOKED
    cred = ConnectorCredential.objects.get(connector_installation=installation)
    assert cred.encrypted_access_token == ""
    assert len(responses.calls) == 1


def test_recording_normalization():
    recordings = normalize_meeting_recordings(_files_payload())
    assert {r.recording_id for r in recordings} == {"rec-audio-1", "rec-video-1"}
    # Prefer audio
    primary = pick_primary_recording(recordings)
    assert primary is not None
    assert primary.recording_id == "rec-audio-1"
    listed = normalize_recordings_list(_files_payload())
    assert len(listed) == 2


@responses.activate
def test_client_list_and_health():
    responses.add(
        responses.GET,
        USERINFO,
        json={"sub": "u1", "name": "Ada", "email": "ada@example.com"},
        status=200,
    )
    responses.add(
        responses.GET,
        f"{DRIVE}files",
        json=_files_payload(),
        status=200,
    )
    client = GoogleMeetClient(api_token="secret")
    health = client.health_check()
    assert health["ok"] is True
    assert health["account_name"] == "Ada"
    assert "secret" not in str(health)
    recordings = client.list_recordings()
    assert len(recordings) == 2

    responses.add(
        responses.GET,
        f"{DRIVE}files/bad",
        json={"error": {"code": 401}},
        status=401,
    )
    with pytest.raises(AuthenticationError, match="401"):
        client.fetch_recording_metadata("bad")


@pytest.mark.django_db(transaction=True)
def test_sync_creates_media(org, monkeypatch):
    installation = _authorized_installation(org)
    client = MagicMock()
    client.list_recordings.return_value = normalize_meeting_recordings(
        _files_payload()
    )

    real_create = __import__(
        "turing.services.media", fromlist=["MediaService"]
    ).MediaService.create_from_url

    def _create_from_url(self, **kwargs):
        assert kwargs["use_case"] == UseCase.MEETING
        assert "gm-access-token" not in str(kwargs)
        return real_create(self, **kwargs)

    monkeypatch.setattr(
        "turing.services.media.MediaService.create_from_url",
        _create_from_url,
    )

    result = GoogleMeetConnector(installation, client=client).sync()
    assert result.records_processed == 1
    asset = MediaAsset.objects.get(
        external_url="https://drive.example/rec-audio-1.m4a"
    )
    ref = ExternalReference.objects.get(
        organization=org,
        external_system="google_meet",
        external_type="meeting",
        external_id="rec-audio-1",
    )
    assert ref.media_id == asset.id

    result2 = GoogleMeetConnector(installation, client=client).sync()
    assert result2.records_processed == 0


@pytest.mark.django_db(transaction=True)
def test_sync_via_service_emits_events(org, settings, monkeypatch):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    seen: list[DomainEvent] = []
    EventBus.subscribe("*", seen.append)
    installation = _authorized_installation(org, name="Meet Events")
    client = MagicMock()
    client.list_recordings.return_value = normalize_meeting_recordings(
        _files_payload()
    )
    original = ConnectorRegistry.create

    def _create(inst):
        if inst.connector_type == "google_meet":
            return GoogleMeetConnector(inst, client=client)
        return original(inst)

    monkeypatch.setattr(ConnectorRegistry, "create", staticmethod(_create))
    job = ConnectorSyncService().start_sync(installation, auto_enqueue=True)
    job.refresh_from_db()
    assert job.status == "completed"
    names = [e.name for e in seen]
    assert EventName.CONNECTOR_SYNC_COMPLETED in names
    assert EventName.MEDIA_CREATED in names
    assert "gm-access-token" not in str([e.payload for e in seen])


@pytest.mark.django_db
def test_sync_failure(org, monkeypatch):
    installation = _authorized_installation(org, name="Meet Fail")
    client = MagicMock()
    client.list_recordings.return_value = normalize_meeting_recordings(
        _files_payload()
    )

    def _boom(*args, **kwargs):
        raise RuntimeError("storage down")

    monkeypatch.setattr("turing.services.media.MediaService.create_from_url", _boom)
    with pytest.raises(ConnectorSyncError, match="storage down"):
        GoogleMeetConnector(installation, client=client).sync()


@pytest.mark.django_db
def test_config_requires_settings(org, settings):
    settings.TURING_GOOGLE_MEET_CLIENT_ID = ""
    settings.TURING_GOOGLE_MEET_CLIENT_SECRET = ""
    installation = _pending_installation(org, name="No Config")
    with pytest.raises(
        ConnectorConfigurationError, match="TURING_GOOGLE_MEET_CLIENT_ID"
    ):
        GoogleMeetConnector(installation).validate_config()


@pytest.mark.django_db
def test_registry_includes_google_meet():
    assert "google_meet" in ConnectorRegistry.types()
    assert ConnectorRegistry.get("google_meet") is GoogleMeetConnector


def test_oauth_client_rejects_missing_credentials():
    with pytest.raises(ConnectorConfigurationError):
        GoogleMeetOAuthClient(client_id="", client_secret="")
