from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from turing.connectors import ConnectorRegistry
from turing.connectors.mock_oauth import MockOAuthConnector
from turing.domain.enums import (
    ConnectorAuthType,
    ConnectorInstallationStatus,
    TuringRole,
)
from turing.models import (
    ConnectorCredential,
    ConnectorInstallation,
    Organization,
    TuringMembership,
)
from turing.security.secrets import ENCRYPTED_PREFIX, is_encrypted
from turing.services.connector_installation import ConnectorInstallationService
from turing.services.credential_encryption import CredentialEncryptionService

User = get_user_model()


@pytest.fixture(autouse=True)
def _registry():
    from turing.connectors.builtins import register_builtin_connectors

    ConnectorRegistry.clear()
    ConnectorRegistry.register(MockOAuthConnector)
    yield
    ConnectorRegistry.clear()
    register_builtin_connectors()


@pytest.fixture
def org(db):
    return Organization.get_default()


def _membership(user, org, role):
    return TuringMembership.objects.create(
        user=user, organization=org, role=role, is_active=True
    )


@pytest.fixture
def admin_user(org):
    user = User.objects.create_user(username="oauth-admin", password="pass")
    _membership(user, org, TuringRole.ADMIN)
    return user


@pytest.fixture
def viewer_user(org):
    user = User.objects.create_user(username="oauth-viewer", password="pass")
    _membership(user, org, TuringRole.VIEWER)
    return user


@pytest.fixture
def client_admin(admin_user):
    client = APIClient()
    client.force_authenticate(user=admin_user)
    return client


def _oauth_installation(org, **kwargs) -> ConnectorInstallation:
    defaults = {
        "organization": org,
        "connector_type": "mock_oauth",
        "name": "Mock OAuth install",
        "status": ConnectorInstallationStatus.PENDING,
        "config": {"client_id": "public-client"},
    }
    defaults.update(kwargs)
    return ConnectorInstallation.objects.create(**defaults)


def test_credential_encryption_round_trip():
    service = CredentialEncryptionService()
    plaintext = "oauth-access-token-super-secret"
    cipher = service.encrypt(plaintext)
    assert cipher != plaintext
    assert is_encrypted(cipher)
    assert ENCRYPTED_PREFIX in cipher
    assert plaintext not in cipher
    assert service.decrypt(cipher) == plaintext
    assert service.encrypt("") == ""
    assert service.decrypt("") == ""


@pytest.mark.django_db
def test_store_credentials_encrypts_at_rest(org):
    installation = _oauth_installation(org)
    service = ConnectorInstallationService()
    secret = "access-token-plain-xyz"
    refresh = "refresh-token-plain-abc"
    cred = service.store_credentials(
        installation,
        access_token=secret,
        refresh_token=refresh,
        expires_at=timezone.now() + timedelta(hours=1),
        auth_type=ConnectorAuthType.OAUTH2,
    )
    cred.refresh_from_db()
    assert is_encrypted(cred.encrypted_access_token)
    assert is_encrypted(cred.encrypted_refresh_token)
    assert secret not in cred.encrypted_access_token
    assert refresh not in cred.encrypted_refresh_token
    assert ENCRYPTED_PREFIX in cred.encrypted_access_token

    connector = MockOAuthConnector(installation)
    assert connector._decrypt_access_token() == secret
    assert connector._decrypt_refresh_token() == refresh


@pytest.mark.django_db
def test_lifecycle_activate_expire_revoke(org):
    installation = _oauth_installation(org)
    service = ConnectorInstallationService()
    service.store_credentials(
        installation,
        access_token="tok-1",
        refresh_token="ref-1",
    )
    service.activate(installation)
    installation.refresh_from_db()
    assert installation.status == ConnectorInstallationStatus.ACTIVE

    service.expire(installation)
    installation.refresh_from_db()
    assert installation.status == ConnectorInstallationStatus.EXPIRED

    service.revoke(installation)
    installation.refresh_from_db()
    cred = ConnectorCredential.objects.get(connector_installation=installation)
    assert installation.status == ConnectorInstallationStatus.REVOKED
    assert cred.encrypted_access_token == ""
    assert cred.encrypted_refresh_token == ""
    assert "revoked_at" in (cred.metadata or {})


@pytest.mark.django_db
def test_one_credential_per_installation(org):
    installation = _oauth_installation(org)
    service = ConnectorInstallationService()
    service.store_credentials(installation, access_token="first")
    service.store_credentials(installation, access_token="second", refresh_token="r2")
    assert ConnectorCredential.objects.filter(
        connector_installation=installation
    ).count() == 1
    assert MockOAuthConnector(installation)._decrypt_access_token() == "second"


@pytest.mark.django_db
def test_api_auth_status_and_revoke(client_admin, org):
    installation = _oauth_installation(org, name="API OAuth")
    ConnectorInstallationService().store_credentials(
        installation,
        access_token="leak-me-access",
        refresh_token="leak-me-refresh",
    )
    ConnectorInstallationService().activate(installation)

    detail = client_admin.get(
        f"/api/turing/v1/connector-installations/{installation.id}/"
    )
    assert detail.status_code == 200
    body = json.dumps(detail.data)
    assert "leak-me-access" not in body
    assert "leak-me-refresh" not in body
    assert "encrypted_access_token" not in body
    assert "encrypted_refresh_token" not in body
    assert "access_token" not in body
    assert detail.data["auth_status"]["auth_type"] == "oauth2"
    assert detail.data["auth_status"]["has_credentials"] is True
    assert detail.data["auth_status"]["status"] == "active"

    patched = client_admin.patch(
        f"/api/turing/v1/connector-installations/{installation.id}/",
        {"status": "revoked"},
        format="json",
    )
    assert patched.status_code == 200
    assert patched.data["status"] == "revoked"
    assert patched.data["auth_status"]["has_credentials"] is False
    body2 = json.dumps(patched.data)
    assert "leak-me-access" not in body2


@pytest.mark.django_db
def test_viewer_cannot_revoke(viewer_user, org):
    installation = _oauth_installation(org)
    ConnectorInstallationService().store_credentials(
        installation, access_token="tok"
    )
    ConnectorInstallationService().activate(installation)
    client = APIClient()
    client.force_authenticate(user=viewer_user)
    response = client.patch(
        f"/api/turing/v1/connector-installations/{installation.id}/",
        {"status": "revoked"},
        format="json",
    )
    assert response.status_code == 403
    installation.refresh_from_db()
    assert installation.status == ConnectorInstallationStatus.ACTIVE


@pytest.mark.django_db
def test_oauth_create_defaults_to_pending(client_admin):
    response = client_admin.post(
        "/api/turing/v1/connector-installations/",
        {
            "connector_type": "mock_oauth",
            "name": "Pending OAuth",
            "config": {},
        },
        format="json",
    )
    assert response.status_code == 201
    assert response.data["status"] == ConnectorInstallationStatus.PENDING
    assert response.data["auth_status"]["auth_type"] == "oauth2"
    assert response.data["auth_status"]["has_credentials"] is False


@pytest.mark.django_db
def test_secret_leakage_not_in_logs(org, caplog):
    import logging

    installation = _oauth_installation(org)
    secret = "very-unique-secret-token-zzz"
    with caplog.at_level(logging.INFO):
        ConnectorInstallationService().store_credentials(
            installation,
            access_token=secret,
            refresh_token=secret,
        )
        ConnectorInstallationService().revoke(installation)
    joined = " ".join(r.message for r in caplog.records)
    assert secret not in joined
