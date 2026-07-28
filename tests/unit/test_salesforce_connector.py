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
from turing.connectors.salesforce.client import SalesforceClient
from turing.connectors.salesforce.connector import SalesforceConnector
from turing.connectors.salesforce.oauth import SalesforceOAuthClient
from turing.connectors.salesforce.serializers import (
    normalize_query_records,
    normalize_record,
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

TOKEN_URL = "https://login.salesforce.com/services/oauth2/token"
REVOKE_URL = "https://login.salesforce.com/services/oauth2/revoke"
REDIRECT = "http://testserver/api/turing/v1/oauth/callback/salesforce/"
INSTANCE = "https://example.my.salesforce.com"
API = f"{INSTANCE}/services/data/v59.0/"


@pytest.fixture(autouse=True)
def _sf_registry(settings):
    settings.TURING_SALESFORCE_CLIENT_ID = "sf-client-id"
    settings.TURING_SALESFORCE_CLIENT_SECRET = "sf-client-secret"
    settings.TURING_SALESFORCE_OAUTH_REDIRECT_URI = REDIRECT
    settings.TURING_SALESFORCE_OAUTH_TOKEN_URL = TOKEN_URL
    settings.TURING_SALESFORCE_OAUTH_REVOKE_URL = REVOKE_URL
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
        "connector_type": "salesforce",
        "name": "Company SF",
        "status": ConnectorInstallationStatus.PENDING,
        "config": {},
    }
    defaults.update(kwargs)
    return ConnectorInstallation.objects.create(**defaults)


def _authorized_installation(org, **kwargs) -> ConnectorInstallation:
    installation = _pending_installation(org, **kwargs)
    ConnectorInstallationService().store_credentials(
        installation,
        access_token="sf-access-token",
        refresh_token="sf-refresh-token",
        expires_at=timezone.now() + timedelta(hours=1),
        metadata={"instance_url": INSTANCE},
    )
    ConnectorInstallationService().activate(installation)
    installation.refresh_from_db()
    return installation


def _query_payload() -> dict[str, Any]:
    return {
        "records": [
            {
                "attributes": {"type": "VoiceCall"},
                "Id": "a00CALL1",
                "Name": "Outbound call",
                "CreatedDate": "2026-01-01T10:00:00.000+0000",
                "RecordingUrl": "https://cdn.example/call-1.mp3",
            },
            {
                "attributes": {"type": "Event"},
                "Id": "a00MTG1",
                "Subject": "Account review",
                "CreatedDate": "2026-01-02T10:00:00.000+0000",
                "RecordingUrl": "https://cdn.example/meeting-1.mp4",
                "Type": "Meeting",
            },
        ]
    }


@pytest.mark.django_db
def test_authorization_url(org):
    installation = _pending_installation(org)
    url = SalesforceConnector(installation).authorization_url()
    parsed = urlparse(url)
    assert "login.salesforce.com" in parsed.netloc
    qs = parse_qs(parsed.query)
    assert qs["client_id"] == ["sf-client-id"]
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
        connector_type="salesforce",
    )
    responses.add(
        responses.POST,
        TOKEN_URL,
        json={
            "access_token": "sf-access-plain",
            "refresh_token": "sf-refresh-plain",
            "instance_url": INSTANCE,
            "token_type": "Bearer",
            "issued_at": "1",
        },
        status=200,
    )
    client = APIClient()
    response = client.get(
        "/api/turing/v1/oauth/callback/salesforce/",
        {"code": "auth-code", "state": state},
    )
    assert response.status_code == 200
    assert "sf-access-plain" not in str(response.data)
    assert response.data["status"] == ConnectorInstallationStatus.ACTIVE
    cred = ConnectorCredential.objects.get(connector_installation=installation)
    assert is_encrypted(cred.encrypted_access_token)
    assert cred.metadata.get("instance_url") == INSTANCE
    assert (
        SalesforceConnector(installation)._decrypt_access_token() == "sf-access-plain"
    )


@responses.activate
@pytest.mark.django_db
def test_token_refresh(org):
    installation = _authorized_installation(org)
    responses.add(
        responses.POST,
        TOKEN_URL,
        json={
            "access_token": "sf-access-new",
            "instance_url": INSTANCE,
            "issued_at": "2",
        },
        status=200,
    )
    cred = ConnectorCredential.objects.get(connector_installation=installation)
    cred.expires_at = timezone.now() - timedelta(minutes=1)
    cred.save(update_fields=["expires_at"])
    connector = SalesforceConnector(installation)
    connector.ensure_fresh_credentials()
    assert connector._decrypt_access_token() == "sf-access-new"
    cred.refresh_from_db()
    assert cred.metadata.get("instance_url") == INSTANCE


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


def test_record_normalization():
    records = normalize_query_records(_query_payload())
    assert len(records) == 2
    by_id = {r.recording_id: r for r in records}
    assert by_id["a00CALL1"].external_type == "call"
    assert by_id["a00MTG1"].external_type == "meeting"
    assert by_id["a00CALL1"].download_url.endswith(".mp3")
    assert normalize_record({"Id": "x"}) is None  # no media url


@responses.activate
def test_client_list_and_health():
    responses.add(
        responses.GET,
        f"{API}chatter/users/me",
        json={"id": "005xx", "displayName": "Ada"},
        status=200,
    )
    responses.add(
        responses.GET,
        f"{API}query",
        json=_query_payload(),
        status=200,
    )
    client = SalesforceClient(api_token="secret", instance_url=INSTANCE)
    health = client.health_check()
    assert health["ok"] is True
    assert health["account_name"] == "Ada"
    assert "secret" not in str(health)
    recordings = client.list_recordings()
    assert len(recordings) == 2

    responses.add(
        responses.GET,
        f"{API}sobjects/VoiceCall/bad",
        json=[{"errorCode": "INVALID"}],
        status=401,
    )
    with pytest.raises(AuthenticationError, match="401"):
        client.fetch_recording_metadata("bad")


@pytest.mark.django_db(transaction=True)
def test_sync_creates_media_and_external_refs(org, monkeypatch):
    installation = _authorized_installation(org)
    client = MagicMock()
    client.list_recordings.return_value = normalize_query_records(_query_payload())

    real_create = __import__(
        "turing.services.media", fromlist=["MediaService"]
    ).MediaService.create_from_url
    use_cases: list[str] = []

    def _create_from_url(self, **kwargs):
        use_cases.append(kwargs["use_case"])
        assert "sf-access-token" not in str(kwargs)
        return real_create(self, **kwargs)

    monkeypatch.setattr(
        "turing.services.media.MediaService.create_from_url",
        _create_from_url,
    )

    result = SalesforceConnector(installation, client=client).sync()
    assert result.records_processed == 2
    assert UseCase.CRM_CALL in use_cases
    assert UseCase.MEETING in use_cases

    call_ref = ExternalReference.objects.get(
        organization=org,
        external_system="salesforce",
        external_type="call",
        external_id="a00CALL1",
    )
    meeting_ref = ExternalReference.objects.get(
        organization=org,
        external_system="salesforce",
        external_type="meeting",
        external_id="a00MTG1",
    )
    assert MediaAsset.objects.filter(id=call_ref.media_id).exists()
    assert MediaAsset.objects.filter(id=meeting_ref.media_id).exists()

    result2 = SalesforceConnector(installation, client=client).sync()
    assert result2.records_processed == 0
    assert result2.details["skipped"] == 2


@pytest.mark.django_db(transaction=True)
def test_sync_via_service_emits_events(org, settings, monkeypatch):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    seen: list[DomainEvent] = []
    EventBus.subscribe("*", seen.append)
    installation = _authorized_installation(org, name="SF Events")
    client = MagicMock()
    client.list_recordings.return_value = normalize_query_records(
        {"records": [_query_payload()["records"][0]]}
    )
    original = ConnectorRegistry.create

    def _create(inst):
        if inst.connector_type == "salesforce":
            return SalesforceConnector(inst, client=client)
        return original(inst)

    monkeypatch.setattr(ConnectorRegistry, "create", staticmethod(_create))
    job = ConnectorSyncService().start_sync(installation, auto_enqueue=True)
    job.refresh_from_db()
    assert job.status == "completed"
    names = [e.name for e in seen]
    assert EventName.CONNECTOR_SYNC_COMPLETED in names
    assert EventName.MEDIA_CREATED in names
    assert "sf-access-token" not in str([e.payload for e in seen])


@pytest.mark.django_db
def test_sync_failure(org, monkeypatch):
    installation = _authorized_installation(org, name="SF Fail")
    client = MagicMock()
    client.list_recordings.return_value = normalize_query_records(
        {"records": [_query_payload()["records"][0]]}
    )

    def _boom(*args, **kwargs):
        raise RuntimeError("storage down")

    monkeypatch.setattr("turing.services.media.MediaService.create_from_url", _boom)
    with pytest.raises(ConnectorSyncError, match="storage down"):
        SalesforceConnector(installation, client=client).sync()


@pytest.mark.django_db
def test_config_requires_settings(org, settings):
    settings.TURING_SALESFORCE_CLIENT_ID = ""
    settings.TURING_SALESFORCE_CLIENT_SECRET = ""
    installation = _pending_installation(org, name="No Config")
    with pytest.raises(
        ConnectorConfigurationError, match="TURING_SALESFORCE_CLIENT_ID"
    ):
        SalesforceConnector(installation).validate_config()


@pytest.mark.django_db
def test_registry_includes_salesforce():
    assert "salesforce" in ConnectorRegistry.types()
    assert ConnectorRegistry.get("salesforce") is SalesforceConnector


def test_oauth_client_rejects_missing_credentials():
    with pytest.raises(ConnectorConfigurationError):
        SalesforceOAuthClient(client_id="", client_secret="")
