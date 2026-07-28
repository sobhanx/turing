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
from turing.connectors.teams.client import TeamsClient
from turing.connectors.teams.connector import TeamsConnector
from turing.connectors.teams.oauth import TeamsOAuthClient
from turing.connectors.teams.serializers import (
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

TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
REVOKE_URL = "https://graph.microsoft.com/v1.0/me/revokeSignInSessions"
REDIRECT = "http://testserver/api/turing/v1/oauth/callback/teams/"
GRAPH = "https://graph.microsoft.com/v1.0/"


@pytest.fixture(autouse=True)
def _teams_registry(settings):
    settings.TURING_TEAMS_CLIENT_ID = "teams-client-id"
    settings.TURING_TEAMS_CLIENT_SECRET = "teams-client-secret"
    settings.TURING_TEAMS_OAUTH_REDIRECT_URI = REDIRECT
    settings.TURING_TEAMS_OAUTH_TOKEN_URL = TOKEN_URL
    settings.TURING_TEAMS_OAUTH_REVOKE_URL = REVOKE_URL
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
        "connector_type": "teams",
        "name": "Company Teams",
        "status": ConnectorInstallationStatus.PENDING,
        "config": {},
    }
    defaults.update(kwargs)
    return ConnectorInstallation.objects.create(**defaults)


def _authorized_installation(org, **kwargs) -> ConnectorInstallation:
    installation = _pending_installation(org, **kwargs)
    ConnectorInstallationService().store_credentials(
        installation,
        access_token="teams-access-token",
        refresh_token="teams-refresh-token",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    ConnectorInstallationService().activate(installation)
    installation.refresh_from_db()
    return installation


def _recording_payload() -> dict[str, Any]:
    return {
        "id": "mtg-1",
        "subject": "Weekly sync",
        "recordings": [
            {
                "id": "rec-audio-1",
                "recordingContentUrl": "https://graph.example/rec-audio-1.m4a",
                "contentType": "audio/mp4",
                "createdDateTime": "2026-01-01T10:00:00Z",
            },
            {
                "id": "rec-video-1",
                "recordingContentUrl": "https://graph.example/rec-video-1.mp4",
                "contentType": "video/mp4",
                "createdDateTime": "2026-01-01T10:00:00Z",
            },
        ],
    }


@pytest.mark.django_db
def test_authorization_url(org):
    installation = _pending_installation(org)
    url = TeamsConnector(installation).authorization_url()
    parsed = urlparse(url)
    assert "login.microsoftonline.com" in parsed.netloc
    qs = parse_qs(parsed.query)
    assert qs["client_id"] == ["teams-client-id"]
    assert qs["response_type"] == ["code"]
    assert qs["redirect_uri"] == [REDIRECT]
    inst_id, org_id = parse_oauth_state(qs["state"][0], consume=False)
    assert inst_id == str(installation.id)
    assert org_id == str(org.id)


@responses.activate
@pytest.mark.django_db
def test_callback_exchange_stores_encrypted_tokens(org):
    installation = _pending_installation(org)
    state = OAuthStateService().generate(
        installation_id=str(installation.id),
        organization_id=org.id,
        connector_type="teams",
    )
    responses.add(
        responses.POST,
        TOKEN_URL,
        json={
            "access_token": "ms-access-plain",
            "refresh_token": "ms-refresh-plain",
            "expires_in": 3600,
            "token_type": "Bearer",
            "scope": "OnlineMeetings.Read",
        },
        status=200,
    )
    client = APIClient()
    response = client.get(
        "/api/turing/v1/oauth/callback/teams/",
        {"code": "auth-code", "state": state},
    )
    assert response.status_code == 200
    assert "ms-access-plain" not in str(response.data)
    assert response.data["status"] == ConnectorInstallationStatus.ACTIVE
    cred = ConnectorCredential.objects.get(connector_installation=installation)
    assert is_encrypted(cred.encrypted_access_token)
    assert TeamsConnector(installation)._decrypt_access_token() == "ms-access-plain"


@responses.activate
@pytest.mark.django_db
def test_refresh_credentials(org):
    installation = _authorized_installation(org)
    responses.add(
        responses.POST,
        TOKEN_URL,
        json={
            "access_token": "ms-access-new",
            "refresh_token": "ms-refresh-new",
            "expires_in": 3600,
        },
        status=200,
    )
    # Force refresh path
    cred = ConnectorCredential.objects.get(connector_installation=installation)
    cred.expires_at = timezone.now() - timedelta(minutes=1)
    cred.save(update_fields=["expires_at"])

    connector = TeamsConnector(installation)
    connector.ensure_fresh_credentials()
    assert connector._decrypt_access_token() == "ms-access-new"


@responses.activate
@pytest.mark.django_db
def test_revoke_credentials(org):
    installation = _authorized_installation(org)
    responses.add(responses.POST, REVOKE_URL, status=200)
    ConnectorInstallationService().revoke(installation)
    installation.refresh_from_db()
    assert installation.status == ConnectorInstallationStatus.REVOKED
    cred = ConnectorCredential.objects.get(connector_installation=installation)
    assert cred.encrypted_access_token == ""
    assert len(responses.calls) == 1


def test_recording_normalization_and_primary():
    recordings = normalize_meeting_recordings(_recording_payload())
    assert {r.recording_id for r in recordings} == {"rec-audio-1", "rec-video-1"}
    primary = pick_primary_recording(recordings)
    assert primary is not None
    assert primary.recording_id == "rec-audio-1"
    listed = normalize_recordings_list({"value": [_recording_payload()]})
    assert len(listed) == 2


@responses.activate
def test_teams_client_list_and_health():
    responses.add(
        responses.GET,
        f"{GRAPH}me",
        json={"id": "u1", "displayName": "Ada"},
        status=200,
    )
    responses.add(
        responses.GET,
        f"{GRAPH}me/onlineMeetings",
        json={"value": [{"id": "mtg-1", "subject": "Weekly sync"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        f"{GRAPH}me/onlineMeetings/mtg-1/recordings",
        json={
            "value": [
                {
                    "id": "rec-audio-1",
                    "recordingContentUrl": "https://graph.example/rec-audio-1.m4a",
                    "contentType": "audio/mp4",
                }
            ]
        },
        status=200,
    )
    client = TeamsClient(api_token="secret")
    health = client.health_check()
    assert health["ok"] is True
    assert health["account_name"] == "Ada"
    assert "secret" not in str(health)
    recordings = client.list_recordings()
    assert len(recordings) == 1
    assert recordings[0].recording_id == "rec-audio-1"

    responses.add(
        responses.GET,
        f"{GRAPH}me/onlineMeetings/bad/recordings",
        json={"error": {"code": "Invalid"}},
        status=401,
    )
    with pytest.raises(AuthenticationError, match="401"):
        client.fetch_recording_metadata("bad")


@pytest.mark.django_db(transaction=True)
def test_sync_creates_media_and_external_ref(org, monkeypatch):
    installation = _authorized_installation(org)
    client = MagicMock()
    client.list_recordings.return_value = normalize_meeting_recordings(
        _recording_payload()
    )

    real_create = __import__(
        "turing.services.media", fromlist=["MediaService"]
    ).MediaService.create_from_url

    def _create_from_url(self, **kwargs):
        assert kwargs["use_case"] == UseCase.MEETING
        assert "teams-access-token" not in str(kwargs)
        return real_create(self, **kwargs)

    monkeypatch.setattr(
        "turing.services.media.MediaService.create_from_url",
        _create_from_url,
    )

    result = TeamsConnector(installation, client=client).sync()
    assert result.records_processed == 1
    asset = MediaAsset.objects.get(external_url="https://graph.example/rec-audio-1.m4a")
    ref = ExternalReference.objects.get(
        organization=org,
        external_system="teams",
        external_type="meeting",
        external_id="rec-audio-1",
    )
    assert ref.media_id == asset.id

    result2 = TeamsConnector(installation, client=client).sync()
    assert result2.records_processed == 0
    assert result2.details["skipped"] == 1


@pytest.mark.django_db(transaction=True)
def test_sync_via_service_emits_events(org, settings, monkeypatch):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    seen: list[DomainEvent] = []
    EventBus.subscribe("*", seen.append)
    installation = _authorized_installation(org, name="Teams Events")
    client = MagicMock()
    client.list_recordings.return_value = normalize_meeting_recordings(
        _recording_payload()
    )
    original = ConnectorRegistry.create

    def _create(inst):
        if inst.connector_type == "teams":
            return TeamsConnector(inst, client=client)
        return original(inst)

    monkeypatch.setattr(ConnectorRegistry, "create", staticmethod(_create))
    job = ConnectorSyncService().start_sync(installation, auto_enqueue=True)
    job.refresh_from_db()
    assert job.status == "completed"
    names = [e.name for e in seen]
    assert EventName.CONNECTOR_SYNC_COMPLETED in names
    assert EventName.MEDIA_CREATED in names
    assert "teams-access-token" not in str([e.payload for e in seen])


@pytest.mark.django_db
def test_sync_failure_when_media_create_fails(org, monkeypatch):
    installation = _authorized_installation(org, name="Teams Fail")
    client = MagicMock()
    client.list_recordings.return_value = normalize_meeting_recordings(
        _recording_payload()
    )

    def _boom(*args, **kwargs):
        raise RuntimeError("storage down")

    monkeypatch.setattr("turing.services.media.MediaService.create_from_url", _boom)
    with pytest.raises(ConnectorSyncError, match="storage down"):
        TeamsConnector(installation, client=client).sync()


@pytest.mark.django_db
def test_config_requires_oauth_settings(org, settings):
    settings.TURING_TEAMS_CLIENT_ID = ""
    settings.TURING_TEAMS_CLIENT_SECRET = ""
    installation = _pending_installation(org, name="No Config")
    with pytest.raises(ConnectorConfigurationError, match="TURING_TEAMS_CLIENT_ID"):
        TeamsConnector(installation).validate_config()


@pytest.mark.django_db
def test_registry_includes_teams():
    assert "teams" in ConnectorRegistry.types()
    assert ConnectorRegistry.get("teams") is TeamsConnector
    assert ConnectorRegistry.get("teams").auth_type == "oauth2"


def test_oauth_client_rejects_missing_credentials():
    with pytest.raises(ConnectorConfigurationError):
        TeamsOAuthClient(client_id="", client_secret="")
