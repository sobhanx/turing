from __future__ import annotations

"""Mock OAuth2 connector for tests and local auth-flow exercises (Phase 4.3.5)."""

from datetime import timedelta
from typing import Any

from django.utils import timezone

from turing.connectors.base import BaseConnector, ConnectorSyncResult, MediaPullItem
from turing.connectors.exceptions import ConnectorConfigurationError, ConnectorError
from turing.domain.enums import ConnectorAuthType
from turing.services.connector_installation import ConnectorInstallationService


class MockOAuthConnector(BaseConnector):
    """
    Test-only OAuth2 connector.

    Does not talk to an external IdP. ``refresh_credentials`` rotates mock tokens
    via ``ConnectorInstallationService.store_credentials``.
    """

    connector_type = "mock_oauth"
    display_name = "Mock OAuth"
    auth_type = ConnectorAuthType.OAUTH2

    def validate_config(self) -> None:
        # Non-secret client metadata may live in config; tokens do not.
        return None

    def validate_credentials(self) -> None:
        super().validate_credentials()
        token = self._decrypt_access_token()
        if token.startswith("revoked:"):
            raise ConnectorConfigurationError("Access token has been revoked.")

    def refresh_credentials(self) -> None:
        refresh = self._decrypt_refresh_token()
        if not refresh:
            raise ConnectorError(
                "No refresh token available.",
                code="connector_refresh_failed",
            )
        service = ConnectorInstallationService()
        service.store_credentials(
            self.installation,
            access_token=f"access-refreshed-{timezone.now().timestamp()}",
            refresh_token=refresh,
            expires_at=timezone.now() + timedelta(hours=1),
            auth_type=ConnectorAuthType.OAUTH2,
            metadata={"refreshed": True},
        )
        service.activate(self.installation)

    def revoke_credentials(self) -> None:
        # Remote revoke would go here; local clear is handled by the service.
        return None

    def health_check(self) -> dict[str, Any]:
        self.validate_credentials()
        return {"ok": True, "auth_type": self.auth_type}

    def pull_media(self, **kwargs: Any) -> list[MediaPullItem]:
        return []

    def sync(self) -> ConnectorSyncResult:
        self.validate_credentials()
        return ConnectorSyncResult(records_processed=0, details={"mock": True})
