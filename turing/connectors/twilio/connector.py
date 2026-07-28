from __future__ import annotations

"""Twilio telephony connector — call recordings → Turing media (api_key)."""

import logging
from typing import Any, ClassVar

from django.conf import settings

from turing.connectors.definition import (
    ConnectorCategory,
    InstallationRequirementField,
    InstallationRequirements,
)
from turing.connectors.exceptions import (
    AuthenticationError,
    ConnectorConfigurationError,
    ConnectorError,
    ConnectorHealthError,
    ConnectorSyncError,
)
from turing.connectors.telephony.connector import TelephonyConnector
from turing.connectors.telephony.serializers import TelephonyCall
from turing.connectors.twilio.client import DEFAULT_API_BASE, TwilioClient
from turing.connectors.twilio.serializers import EXTERNAL_SYSTEM
from turing.domain.enums import ConnectorAuthType

logger = logging.getLogger(__name__)


def _twilio_settings() -> dict[str, str]:
    return {
        "account_sid": str(getattr(settings, "TURING_TWILIO_ACCOUNT_SID", "") or ""),
        "auth_token": str(getattr(settings, "TURING_TWILIO_AUTH_TOKEN", "") or ""),
        "api_base": str(
            getattr(settings, "TURING_TWILIO_API_BASE", "") or DEFAULT_API_BASE
        ),
    }


class TwilioConnector(TelephonyConnector):
    """
    Twilio call recordings → Turing media (Account SID + Auth Token).

    Credentials come from installation ``config`` (preferred) or host settings
    ``TURING_TWILIO_ACCOUNT_SID`` / ``TURING_TWILIO_AUTH_TOKEN``. Secrets are
    write-only: never returned by API serializers and never logged.
    """

    connector_type = "twilio"
    display_name = "Twilio"
    description = (
        "Sync Twilio call recordings into Turing for transcription and analysis."
    )
    provider = "Twilio"
    category = ConnectorCategory.TELEPHONY
    documentation_url = "https://www.twilio.com/docs/voice/api/recording"
    auth_type = ConnectorAuthType.API_KEY
    supports_oauth = False
    supports_refresh = False
    supports_revoke = False
    supported_sync_types: ClassVar[tuple[str, ...]] = ("calls",)
    external_system: ClassVar[str] = EXTERNAL_SYSTEM
    installation_requirements = InstallationRequirements(
        config_fields=(
            InstallationRequirementField(
                key="account_sid",
                label="Account SID",
                required=False,
                description=(
                    "Optional per-installation Account SID. Falls back to "
                    "TURING_TWILIO_ACCOUNT_SID."
                ),
                validation_message="Twilio Account SID is required.",
            ),
            InstallationRequirementField(
                key="auth_token",
                label="Auth Token",
                required=False,
                secret=True,
                description=(
                    "Optional per-installation Auth Token. Falls back to "
                    "TURING_TWILIO_AUTH_TOKEN."
                ),
                validation_message="Twilio Auth Token is required.",
            ),
        ),
        messages=(
            "Configure Twilio Account SID and Auth Token on the host "
            "(TURING_TWILIO_*), or provide account_sid / auth_token in "
            "installation config (write-only).",
            "Recording media URLs must be reachable by Turing media ingest.",
        ),
    )

    def __init__(
        self,
        installation,
        *,
        client: TwilioClient | None = None,
    ) -> None:
        super().__init__(installation)
        self._client = client

    @property
    def name(self) -> str:
        return "twilio"

    def _resolve_credentials(self) -> tuple[str, str, str]:
        host = _twilio_settings()
        account_sid = str(
            self.config.get("account_sid") or host["account_sid"] or ""
        ).strip()
        auth_token = str(
            self.config.get("auth_token")
            or self.config.get("api_token")
            or host["auth_token"]
            or ""
        ).strip()
        api_base = str(
            self.config.get("api_base") or host["api_base"] or DEFAULT_API_BASE
        ).strip()
        return account_sid, auth_token, api_base

    def validate_config(self) -> None:
        account_sid, auth_token, _api_base = self._resolve_credentials()
        if not account_sid:
            raise ConnectorConfigurationError(
                "Twilio Account SID is required "
                "(installation config account_sid or TURING_TWILIO_ACCOUNT_SID)."
            )
        if not auth_token:
            raise ConnectorConfigurationError(
                "Twilio Auth Token is required "
                "(installation config auth_token or TURING_TWILIO_AUTH_TOKEN)."
            )

    def validate_credentials(self) -> None:
        """Validate Account SID / Auth Token against the Twilio Account API."""
        self.validate_config()
        try:
            self._build_client().authenticate()
        except AuthenticationError:
            raise
        except ConnectorError as exc:
            raise AuthenticationError(
                "Twilio credential validation failed.",
                code=getattr(exc, "code", "twilio_auth_failed"),
            ) from exc

    def _build_client(self) -> TwilioClient:
        if self._client is not None:
            return self._client
        account_sid, auth_token, api_base = self._resolve_credentials()
        return TwilioClient(
            account_sid=account_sid,
            auth_token=auth_token,
            api_base=api_base,
        )

    def health_check(self) -> dict[str, Any]:
        self.validate_config()
        try:
            result = self._build_client().health_check()
        except ConnectorError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ConnectorHealthError(f"Twilio health check failed: {exc}") from exc
        return {
            "ok": bool(result.get("ok")),
            "account_sid": result.get("account_sid") or "",
            "friendly_name": result.get("friendly_name") or "",
            "status": result.get("status") or "",
        }

    def list_calls(self, **kwargs: Any) -> list[TelephonyCall]:
        self.validate_config()
        client = self._build_client()
        page_size = int(kwargs.get("page_size") or self.config.get("page_size") or 50)
        try:
            return client.list_calls_with_recordings(page_size=page_size)
        except ConnectorError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ConnectorSyncError(f"Twilio list_calls failed: {exc}") from exc

    def get_recording(self, call_id: str) -> TelephonyCall | None:
        self.validate_config()
        try:
            return self._build_client().get_recording_for_call(call_id)
        except ConnectorError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ConnectorSyncError(
                f"Twilio get_recording failed for '{call_id}': {exc}"
            ) from exc

    def sync(self):
        """Validate credentials then ingest via TelephonyConnector sync path."""
        try:
            self.validate_credentials()
        except AuthenticationError:
            raise
        except ConnectorError as exc:
            raise ConnectorSyncError(str(exc)) from exc
        return super().sync()
