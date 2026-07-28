from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from turing.connectors import (
    BaseConnector,
    ConnectorCategory,
    ConnectorConfigurationError,
    ConnectorDefinition,
    ConnectorRegistry,
    ConnectorSyncResult,
    InstallationRequirementField,
    InstallationRequirements,
    MediaPullItem,
)
from turing.connectors.builtins import register_builtin_connectors
from turing.connectors.definition import split_scopes
from turing.domain.enums import TuringRole
from turing.models import Organization, TuringMembership

User = get_user_model()


class MarketFakeConnector(BaseConnector):
    connector_type = "market-fake"
    display_name = "Market Fake"
    description = "Test marketplace connector"
    provider = "Turing Labs"
    category = ConnectorCategory.MEETINGS
    documentation_url = "https://example.com/docs/market-fake"
    icon_url = "https://example.com/icons/market-fake.svg"
    supported_sync_types = ("media", "metadata")
    required_scopes = ("recording:read", "user:read")
    installation_requirements = InstallationRequirements(
        oauth_scopes=("recording:read", "user:read"),
        config_fields=(
            InstallationRequirementField(
                key="account_id",
                label="Account ID",
                validation_message="Account ID is required.",
            ),
            InstallationRequirementField(
                key="api_token",
                label="API token",
                secret=True,
                validation_message="API token is required.",
            ),
        ),
        messages=("Host must configure marketplace credentials.",),
    )

    def validate_config(self) -> None:
        if not self.config.get("account_id"):
            raise ConnectorConfigurationError("account_id is required.")

    def health_check(self) -> dict[str, Any]:
        return {"ok": True}

    def pull_media(self, **kwargs: Any) -> list[MediaPullItem]:
        return []

    def sync(self) -> ConnectorSyncResult:
        return ConnectorSyncResult(records_processed=0)


def _membership(user, org, role):
    return TuringMembership.objects.create(
        user=user, organization=org, role=role, is_active=True
    )


@pytest.fixture(autouse=True)
def _registry():
    ConnectorRegistry.clear()
    ConnectorRegistry.register(MarketFakeConnector)
    yield
    ConnectorRegistry.clear()
    register_builtin_connectors()


@pytest.fixture
def org(db):
    return Organization.get_default()


@pytest.fixture
def admin_client(org):
    user = User.objects.create_user(username="mkt-admin", password="pass")
    _membership(user, org, TuringRole.ADMIN)
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def test_definition_metadata_validation():
    definition = MarketFakeConnector.definition()
    assert isinstance(definition, ConnectorDefinition)
    definition.validate()
    assert definition.provider == "Turing Labs"
    assert definition.category == ConnectorCategory.MEETINGS
    assert definition.required_scopes == ("recording:read", "user:read")

    with pytest.raises(ConnectorConfigurationError, match="display_name"):
        ConnectorDefinition(
            connector_type="broken",
            display_name="",
        ).validate()


def test_registry_get_definition_and_validate_requirements():
    definition = ConnectorRegistry.get_definition("market-fake")
    assert definition.display_name == "Market Fake"
    assert definition.to_catalog_dict()["provider"] == "Turing Labs"

    ConnectorRegistry.validate_installation_requirements(
        "market-fake",
        {"account_id": "a1", "api_token": "secret-value"},
        scopes_granted=("recording:read", "user:read"),
    )

    with pytest.raises(ConnectorConfigurationError, match="Account ID"):
        ConnectorRegistry.validate_installation_requirements(
            "market-fake",
            {"api_token": "secret-value"},
        )

    with pytest.raises(ConnectorConfigurationError, match="Missing required OAuth"):
        ConnectorRegistry.validate_installation_requirements(
            "market-fake",
            {"account_id": "a1", "api_token": "x"},
            scopes_granted=("recording:read",),
        )


def test_no_secret_leakage_in_definition_or_catalog():
    catalog = ConnectorRegistry.list_available()
    blob = str(catalog).lower()
    assert "secret-value" not in blob
    assert "client_secret" not in blob
    assert "access_token" not in blob
    row = catalog[0]
    assert row["installation_requirements"]["config_fields"][1]["secret"] is True
    assert "secret-value" not in str(row)


@pytest.mark.django_db
def test_catalog_api_marketplace_contract(admin_client):
    response = admin_client.get("/api/turing/v1/connectors/")
    assert response.status_code == 200
    assert len(response.data) == 1
    row = response.data[0]
    for key in (
        "connector_type",
        "display_name",
        "provider",
        "description",
        "auth_type",
        "capabilities",
        "supported_sync_types",
        "installation_requirements",
    ):
        assert key in row
    assert row["connector_type"] == "market-fake"
    assert row["provider"] == "Turing Labs"
    assert row["description"] == "Test marketplace connector"
    assert row["category"] == "meetings"
    assert row["documentation_url"].startswith("https://")
    assert row["icon_url"].endswith(".svg")
    assert row["required_scopes"] == ["recording:read", "user:read"]
    assert row["capabilities"]["oauth"] is False
    assert row["supported_sync_types"] == ["media", "metadata"]
    reqs = row["installation_requirements"]
    assert reqs["oauth_scopes"] == ["recording:read", "user:read"]
    assert reqs["messages"] == ["Host must configure marketplace credentials."]
    assert reqs["config_fields"][0]["key"] == "account_id"
    assert reqs["config_fields"][1]["secret"] is True
    assert "secret-value" not in str(response.data)
    assert "password" not in str(response.data).lower()


@pytest.mark.django_db
def test_builtin_connector_regression():
    ConnectorRegistry.clear()
    register_builtin_connectors()
    types = ConnectorRegistry.types()
    assert types == ["google_meet", "salesforce", "teams", "zoom"]

    catalog = ConnectorRegistry.list_available()
    by_type = {row["connector_type"]: row for row in catalog}
    assert by_type["zoom"]["provider"] == "Zoom"
    assert by_type["zoom"]["category"] == "meetings"
    assert "recording:read" in by_type["zoom"]["required_scopes"]
    assert by_type["teams"]["provider"] == "Microsoft"
    assert by_type["google_meet"]["provider"] == "Google"
    assert by_type["salesforce"]["category"] == "crm"
    assert by_type["salesforce"]["capabilities"]["oauth"] is True

    for row in catalog:
        assert "client_secret" not in str(row).lower()
        assert isinstance(row["installation_requirements"], dict)
        assert "oauth_scopes" in row["installation_requirements"]
        ConnectorRegistry.get_definition(row["connector_type"]).validate()


def test_split_scopes_helper():
    assert split_scopes("a b,c") == ("a", "b", "c")
    assert split_scopes(["x", " y "]) == ("x", "y")
