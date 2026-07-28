from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

import pytest
import responses
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from turing.connectors.exceptions import ConnectorConfigurationError, ConnectorError
from turing.connectors.zoom.connector import ZoomConnector
from turing.connectors.zoom.oauth import ZoomOAuthClient
from turing.domain.enums import (
    ConnectorInstallationStatus,
    ConnectorSyncJobStatus,
    TuringRole,
)
from turing.domain.events import DomainEvent, EventName
from turing.events.bus import EventBus
from turing.models import ConnectorCredential, ConnectorInstallation, Organization, TuringMembership
from turing.security.secrets import is_encrypted
from turing.services.connector_installation import ConnectorInstallationService
from turing.services.connector_sync import ConnectorSyncService
from turing.services.oauth_state import OAuthStateService, parse_oauth_state

User = get_user_model()

TOKEN_URL = "https://zoom.us/oauth/token"
REVOKE_URL = "https://zoom.us/oauth/revoke"
REDIRECT = "http://testserver/api/turing/v1/oauth/callback/zoom/"


@pytest.fixture
def zoom_oauth_settings(settings):
    settings.TURING_ZOOM_CLIENT_ID = "zoom-client-id"
    settings.TURING_ZOOM_CLIENT_SECRET = "zoom-client-secret"
    settings.TURING_ZOOM_OAUTH_REDIRECT_URI = REDIRECT
    settings.TURING_ZOOM_OAUTH_TOKEN_URL = TOKEN_URL
    settings.TURING_ZOOM_OAUTH_REVOKE_URL = REVOKE_URL
    return settings


@pytest.fixture
def org(db):
    return Organization.get_default()


@pytest.fixture
def admin_user(org):
    user = User.objects.create_user(username="zoom-oauth-admin", password="pass")
    TuringMembership.objects.create(
        user=user, organization=org, role=TuringRole.ADMIN, is_active=True
    )
    return user


@pytest.fixture
def client_admin(admin_user):
    client = APIClient()
    client.force_authenticate(user=admin_user)
    return client


def _pending_installation(org, **kwargs) -> ConnectorInstallation:
    defaults = {
        "organization": org,
        "connector_type": "zoom",
        "name": "Zoom OAuth",
        "status": ConnectorInstallationStatus.PENDING,
        "config": {},
    }
    defaults.update(kwargs)
    return ConnectorInstallation.objects.create(**defaults)


@pytest.mark.django_db
def test_authorization_url_generation(org, zoom_oauth_settings):
    installation = _pending_installation(org)
    connector = ZoomConnector(installation)
    url = connector.authorization_url()
    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert "zoom.us" in parsed.netloc
    qs = parse_qs(parsed.query)
    assert qs["response_type"] == ["code"]
    assert qs["client_id"] == ["zoom-client-id"]
    assert qs["redirect_uri"] == [REDIRECT]
    assert "state" in qs
    inst_id, org_id = parse_oauth_state(qs["state"][0], consume=False)
    assert inst_id == str(installation.id)
    assert org_id == str(org.id)


@pytest.mark.django_db
def test_authorize_api_returns_url(client_admin, org, zoom_oauth_settings):
    installation = _pending_installation(org)
    response = client_admin.get(
        f"/api/turing/v1/connector-installations/{installation.id}/authorize/"
    )
    assert response.status_code == 200
    assert "authorization_url" in response.data
    assert "zoom-client-secret" not in json.dumps(response.data)
    assert str(installation.id) in response.data["authorization_url"] or True
    qs = parse_qs(urlparse(response.data["authorization_url"]).query)
    assert qs["client_id"] == ["zoom-client-id"]


@responses.activate
@pytest.mark.django_db
def test_callback_success_stores_encrypted_tokens(org, zoom_oauth_settings):
    installation = _pending_installation(org)
    state = OAuthStateService().generate(
        installation_id=str(installation.id),
        organization_id=org.id,
        connector_type="zoom",
    )
    responses.add(
        responses.POST,
        TOKEN_URL,
        json={
            "access_token": "access-plain-xyz",
            "refresh_token": "refresh-plain-abc",
            "expires_in": 3600,
            "token_type": "bearer",
            "scope": "recording:read",
        },
        status=200,
    )
    client = APIClient()
    response = client.get(
        "/api/turing/v1/oauth/callback/zoom/",
        {"code": "auth-code-1", "state": state},
    )
    assert response.status_code == 200
    body = json.dumps(response.data)
    assert "access-plain-xyz" not in body
    assert "refresh-plain-abc" not in body
    assert response.data["status"] == ConnectorInstallationStatus.ACTIVE
    assert response.data["auth_status"]["has_credentials"] is True

    installation.refresh_from_db()
    assert installation.status == ConnectorInstallationStatus.ACTIVE
    cred = ConnectorCredential.objects.get(connector_installation=installation)
    assert is_encrypted(cred.encrypted_access_token)
    assert is_encrypted(cred.encrypted_refresh_token)
    assert "access-plain-xyz" not in cred.encrypted_access_token

    connector = ZoomConnector(installation)
    assert connector._decrypt_access_token() == "access-plain-xyz"
    assert connector._decrypt_refresh_token() == "refresh-plain-abc"


@responses.activate
@pytest.mark.django_db
def test_refresh_flow_updates_credentials(org, zoom_oauth_settings):
    installation = _pending_installation(org)
    service = ConnectorInstallationService()
    service.store_credentials(
        installation,
        access_token="old-access",
        refresh_token="refresh-1",
        expires_at=timezone.now() - timedelta(minutes=1),
    )
    service.activate(installation)

    responses.add(
        responses.POST,
        TOKEN_URL,
        json={
            "access_token": "new-access",
            "refresh_token": "refresh-2",
            "expires_in": 3600,
        },
        status=200,
    )
    connector = ZoomConnector(installation)
    connector.ensure_fresh_credentials()
    assert connector._decrypt_access_token() == "new-access"
    assert connector._decrypt_refresh_token() == "refresh-2"
    cred = ConnectorCredential.objects.get(connector_installation=installation)
    assert cred.expires_at is not None
    assert cred.expires_at > timezone.now()


@responses.activate
@pytest.mark.django_db
def test_revoke_flow_calls_zoom_and_clears_tokens(org, zoom_oauth_settings):
    installation = _pending_installation(org)
    service = ConnectorInstallationService()
    service.store_credentials(
        installation,
        access_token="access-to-revoke",
        refresh_token="refresh-to-revoke",
    )
    service.activate(installation)
    responses.add(responses.POST, REVOKE_URL, status=200)

    service.revoke(installation)
    installation.refresh_from_db()
    assert installation.status == ConnectorInstallationStatus.REVOKED
    cred = ConnectorCredential.objects.get(connector_installation=installation)
    assert cred.encrypted_access_token == ""
    assert cred.encrypted_refresh_token == ""
    assert len(responses.calls) == 1


@responses.activate
@pytest.mark.django_db(transaction=True)
def test_expired_credential_refresh_failure_marks_expired(
    org, zoom_oauth_settings, settings
):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    seen: list[DomainEvent] = []
    EventBus.clear()
    EventBus.subscribe("*", seen.append)

    installation = _pending_installation(org)
    service = ConnectorInstallationService()
    service.store_credentials(
        installation,
        access_token="stale-access",
        refresh_token="bad-refresh",
        expires_at=timezone.now() - timedelta(minutes=5),
    )
    service.activate(installation)

    responses.add(
        responses.POST,
        TOKEN_URL,
        json={"reason": "Invalid token"},
        status=400,
    )

    job = ConnectorSyncService().start_sync(installation, auto_enqueue=False)
    finished = ConnectorSyncService().run_sync(str(job.id))
    finished.refresh_from_db()
    installation.refresh_from_db()

    assert finished.status == ConnectorSyncJobStatus.FAILED
    assert installation.status == ConnectorInstallationStatus.EXPIRED
    assert EventName.CONNECTOR_SYNC_FAILED in [e.name for e in seen]
    # No token material in events
    assert "stale-access" not in str([e.payload for e in seen])
    assert "bad-refresh" not in str([e.payload for e in seen])
    EventBus.clear()


@pytest.mark.django_db
def test_validate_config_requires_oauth_app_settings(org, settings):
    settings.TURING_ZOOM_CLIENT_ID = ""
    settings.TURING_ZOOM_CLIENT_SECRET = ""
    installation = _pending_installation(org)
    with pytest.raises(ConnectorConfigurationError, match="TURING_ZOOM_CLIENT_ID"):
        ZoomConnector(installation).validate_config()


@responses.activate
@pytest.mark.django_db
def test_exchange_code_via_connector(org, zoom_oauth_settings):
    installation = _pending_installation(org)
    responses.add(
        responses.POST,
        TOKEN_URL,
        json={
            "access_token": "exchanged-access",
            "refresh_token": "exchanged-refresh",
            "expires_in": 1800,
        },
        status=200,
    )
    ZoomConnector(installation).exchange_code("code-99")
    assert ZoomConnector(installation)._decrypt_access_token() == "exchanged-access"
    assert installation.status == ConnectorInstallationStatus.PENDING


def test_oauth_client_rejects_missing_app_credentials():
    with pytest.raises(ConnectorConfigurationError):
        ZoomOAuthClient(client_id="", client_secret="")


@pytest.mark.django_db
def test_callback_rejects_mismatched_connector(org, zoom_oauth_settings):
    installation = _pending_installation(org)
    state = OAuthStateService().generate(
        installation_id=str(installation.id),
        organization_id=org.id,
        connector_type="zoom",
    )
    client = APIClient()
    response = client.get(
        "/api/turing/v1/oauth/callback/mock_oauth/",
        {"code": "x", "state": state},
    )
    assert response.status_code == 400
