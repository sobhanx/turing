from __future__ import annotations

"""Salesforce CRM call/meeting recording → Turing media connector (OAuth2)."""

import logging
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.utils import timezone

from turing.connectors.base import BaseConnector, ConnectorSyncResult, MediaPullItem
from turing.connectors.definition import (
    ConnectorCategory,
    InstallationRequirements,
    split_scopes,
)
from turing.connectors.exceptions import (
    AuthenticationError,
    ConnectorConfigurationError,
    ConnectorError,
    ConnectorHealthError,
    ConnectorSyncError,
)
from turing.connectors.salesforce.client import SalesforceClient
from turing.connectors.salesforce.oauth import DEFAULT_SCOPES, SalesforceOAuthClient
from turing.domain.enums import ConnectorAuthType, UseCase
from turing.services.connector_installation import ConnectorInstallationService
from turing.services.oauth_state import OAuthStateService

logger = logging.getLogger(__name__)

EXTERNAL_SYSTEM = "salesforce"
_TOKEN_SKEW = timedelta(seconds=60)


def _salesforce_oauth_settings() -> dict[str, str]:
    return {
        "client_id": str(getattr(settings, "TURING_SALESFORCE_CLIENT_ID", "") or ""),
        "client_secret": str(
            getattr(settings, "TURING_SALESFORCE_CLIENT_SECRET", "") or ""
        ),
        "authorize_url": str(
            getattr(settings, "TURING_SALESFORCE_OAUTH_AUTHORIZE_URL", "") or ""
        ),
        "token_url": str(
            getattr(settings, "TURING_SALESFORCE_OAUTH_TOKEN_URL", "") or ""
        ),
        "revoke_url": str(
            getattr(settings, "TURING_SALESFORCE_OAUTH_REVOKE_URL", "") or ""
        ),
        "redirect_uri": str(
            getattr(settings, "TURING_SALESFORCE_OAUTH_REDIRECT_URI", "") or ""
        ),
        "scopes": str(getattr(settings, "TURING_SALESFORCE_OAUTH_SCOPES", "") or "")
        or DEFAULT_SCOPES,
    }


class SalesforceConnector(BaseConnector):
    """
    Salesforce CRM call/meeting recordings → Turing media (OAuth2).

    Tokens + ``instance_url`` live on ``ConnectorCredential`` (encrypted tokens;
    instance_url in non-secret metadata). Sync refreshes expired access tokens.
    """

    connector_type = "salesforce"
    display_name = "Salesforce"
    description = (
        "Discover Salesforce call and meeting recordings and ingest them into Turing."
    )
    provider = "Salesforce"
    category = ConnectorCategory.CRM
    documentation_url = "https://developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_rest/"
    auth_type = ConnectorAuthType.OAUTH2
    supports_oauth = True
    supports_refresh = True
    supports_revoke = True
    supported_sync_types = ("media",)
    required_scopes = split_scopes(DEFAULT_SCOPES)
    installation_requirements = InstallationRequirements(
        oauth_scopes=split_scopes(DEFAULT_SCOPES),
        messages=(
            "Configure Salesforce OAuth client settings on the host "
            "(TURING_SALESFORCE_*).",
            "Set TURING_SALESFORCE_OAUTH_REDIRECT_URI to the Turing OAuth callback.",
            "Complete OAuth authorization after creating the installation.",
        ),
    )

    def __init__(
        self,
        installation,
        *,
        client: SalesforceClient | None = None,
        oauth_client: SalesforceOAuthClient | None = None,
    ) -> None:
        super().__init__(installation)
        self._client = client
        self._oauth_client = oauth_client

    @property
    def name(self) -> str:
        return "salesforce"

    def _oauth(self) -> SalesforceOAuthClient:
        if self._oauth_client is not None:
            return self._oauth_client
        cfg = _salesforce_oauth_settings()
        kwargs: dict[str, Any] = {
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
        }
        if cfg["authorize_url"]:
            kwargs["authorize_url"] = cfg["authorize_url"]
        if cfg["token_url"]:
            kwargs["token_url"] = cfg["token_url"]
        if cfg["revoke_url"]:
            kwargs["revoke_url"] = cfg["revoke_url"]
        return SalesforceOAuthClient(**kwargs)

    def _redirect_uri(self, redirect_uri: str | None = None) -> str:
        uri = (
            (redirect_uri or "").strip()
            or _salesforce_oauth_settings()["redirect_uri"]
        )
        if not uri:
            raise ConnectorConfigurationError(
                "Salesforce OAuth redirect URI is not configured. "
                "Set TURING_SALESFORCE_OAUTH_REDIRECT_URI."
            )
        return uri

    def validate_config(self) -> None:
        cfg = _salesforce_oauth_settings()
        if not cfg["client_id"] or not cfg["client_secret"]:
            raise ConnectorConfigurationError(
                "Salesforce OAuth is not configured. Set "
                "TURING_SALESFORCE_CLIENT_ID and TURING_SALESFORCE_CLIENT_SECRET."
            )

    def validate_credentials(self) -> None:
        self.validate_config()
        super().validate_credentials()
        if not self._instance_url():
            raise ConnectorConfigurationError(
                "Salesforce instance_url is missing. Re-authorize the connector."
            )

    def authorization_url(
        self, *, redirect_uri: str | None = None, state: str | None = None
    ) -> str:
        self.validate_config()
        redirect = self._redirect_uri(redirect_uri)
        state_value = state or OAuthStateService().generate(
            installation_id=str(self.installation.id),
            organization_id=self.installation.organization_id,
            connector_type=self.connector_type,
        )
        return self._oauth().build_authorization_url(
            redirect_uri=redirect,
            state=state_value,
            scopes=_salesforce_oauth_settings()["scopes"],
        )

    def exchange_code(self, code: str, *, redirect_uri: str | None = None) -> None:
        self.validate_config()
        redirect = self._redirect_uri(redirect_uri)
        payload = self._oauth().exchange_code(code, redirect_uri=redirect)
        self._persist_token_payload(payload)

    def refresh_credentials(self) -> None:
        self.validate_config()
        refresh = self._decrypt_refresh_token()
        if not refresh:
            ConnectorInstallationService().expire(self.installation)
            raise AuthenticationError("Salesforce refresh token is missing.")
        try:
            payload = self._oauth().refresh_token(refresh)
        except ConnectorError as exc:
            ConnectorInstallationService().expire(self.installation)
            raise AuthenticationError(
                "Salesforce OAuth token refresh failed.",
                code=getattr(exc, "code", "salesforce_oauth_refresh_failed"),
            ) from exc
        self._persist_token_payload(payload)

    def revoke_credentials(self) -> None:
        try:
            token = self._decrypt_access_token() or self._decrypt_refresh_token()
            if token:
                self._oauth().revoke_token(token)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Salesforce revoke_credentials failed installation_id=%s",
                getattr(self.installation, "id", None),
            )

    def ensure_fresh_credentials(self) -> None:
        self.validate_config()
        cred = self._credential()
        if cred is None or not cred.has_access_token():
            raise ConnectorConfigurationError(
                "OAuth access token is missing. Complete authorization first."
            )
        expires_at = cred.expires_at
        if expires_at is not None and expires_at <= timezone.now() + _TOKEN_SKEW:
            logger.info(
                "Refreshing Salesforce OAuth token installation_id=%s",
                self.installation.id,
            )
            try:
                self.refresh_credentials()
            except AuthenticationError:
                raise
            except ConnectorError as exc:
                ConnectorInstallationService().expire(self.installation)
                raise AuthenticationError(
                    "Salesforce OAuth token refresh failed.",
                    code=getattr(exc, "code", "salesforce_oauth_refresh_failed"),
                ) from exc
        self.validate_credentials()

    def _instance_url(self) -> str:
        cfg_url = str(self.config.get("instance_url") or "").strip()
        if cfg_url:
            return cfg_url.rstrip("/")
        cred = self._credential()
        if cred is None:
            return ""
        meta = dict(cred.metadata or {})
        return str(meta.get("instance_url") or "").strip().rstrip("/")

    def _persist_token_payload(self, payload: dict[str, Any]) -> None:
        access = str(payload.get("access_token") or "")
        refresh = str(payload.get("refresh_token") or "") or self._decrypt_refresh_token()
        expires_in = payload.get("expires_in")
        expires_at = None
        try:
            if expires_in is not None:
                expires_at = timezone.now() + timedelta(seconds=int(expires_in))
        except (TypeError, ValueError):
            expires_at = None

        existing_meta: dict[str, Any] = {}
        cred = self._credential()
        if cred is not None:
            existing_meta = dict(cred.metadata or {})

        instance_url = str(
            payload.get("instance_url")
            or existing_meta.get("instance_url")
            or self.config.get("instance_url")
            or ""
        ).strip().rstrip("/")

        meta = {
            **existing_meta,
            **{
                k: v
                for k, v in {
                    "scope": str(payload.get("scope") or ""),
                    "token_type": str(payload.get("token_type") or ""),
                    "instance_url": instance_url,
                    "id": str(payload.get("id") or ""),
                }.items()
                if v
            },
        }
        # Never store tokens in metadata.
        for secret_key in ("access_token", "refresh_token", "signature"):
            meta.pop(secret_key, None)

        ConnectorInstallationService().store_credentials(
            self.installation,
            access_token=access,
            refresh_token=refresh,
            expires_at=expires_at,
            auth_type=ConnectorAuthType.OAUTH2,
            metadata=meta,
        )

    def _build_client(self) -> SalesforceClient:
        if self._client is not None:
            return self._client
        token = self._decrypt_access_token()
        if not token:
            raise ConnectorConfigurationError(
                "OAuth access token is missing. Complete authorization first."
            )
        instance_url = self._instance_url()
        if not instance_url:
            raise ConnectorConfigurationError(
                "Salesforce instance_url is missing. Re-authorize the connector."
            )
        return SalesforceClient(
            api_token=token,
            instance_url=instance_url,
            api_version=str(self.config.get("api_version") or "v59.0"),
        )

    def health_check(self) -> dict[str, Any]:
        self.ensure_fresh_credentials()
        try:
            result = self._build_client().health_check()
        except ConnectorError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ConnectorHealthError(
                f"Salesforce health check failed: {exc}"
            ) from exc
        return {
            "ok": bool(result.get("ok")),
            "account_name": result.get("account_name") or "",
            "user_id": result.get("user_id") or "",
            "instance_url": result.get("instance_url") or "",
        }

    def pull_media(self, **kwargs: Any) -> list[MediaPullItem]:
        self.ensure_fresh_credentials()
        client = self._build_client()
        recordings = client.list_recordings(
            from_date=kwargs.get("from_date"),
            to_date=kwargs.get("to_date"),
            soql=kwargs.get("soql"),
        )
        items: list[MediaPullItem] = []
        for recording in recordings:
            ext = recording.file_extension or "mp3"
            filename = f"salesforce-{recording.recording_id}.{ext}"
            items.append(
                MediaPullItem(
                    external_id=recording.recording_id,
                    source_url=recording.download_url,
                    filename=filename,
                    metadata={
                        "external_system": EXTERNAL_SYSTEM,
                        "external_type": recording.external_type,
                        "external_id": recording.recording_id,
                        "media_url": recording.download_url,
                        "topic": recording.topic,
                        "file_type": recording.file_type,
                        "file_size": recording.file_size,
                        "recording_start": recording.recording_start,
                        "recording_end": recording.recording_end,
                        **dict(recording.metadata or {}),
                    },
                )
            )
        return items

    def sync(self) -> ConnectorSyncResult:
        try:
            items = self.pull_media()
        except ConnectorError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ConnectorSyncError(
                f"Salesforce pull_media failed: {exc}"
            ) from exc

        from turing.connectors.media_ingest import sync_media_pull_items

        def _auth():
            token = self._decrypt_access_token()
            if token:
                return {"Authorization": f"Bearer {token}"}, None
            return None, None

        def _type(item):
            return str(
                (item.metadata or {}).get("external_type") or "call"
            ).strip() or "call"

        def _use_case(item):
            return UseCase.MEETING if _type(item) == "meeting" else UseCase.CRM_CALL

        return sync_media_pull_items(
            installation=self.installation,
            items=items,
            external_system=EXTERNAL_SYSTEM,
            external_type=_type,
            use_case=_use_case,
            metadata_namespace="salesforce",
            default_filename=lambda item: f"salesforce-{item.external_id}.mp3",
            download_auth=_auth,
            attach_metadata=lambda item: {
                "topic": (item.metadata or {}).get("topic", ""),
                "who_id": (item.metadata or {}).get("who_id", ""),
                "what_id": (item.metadata or {}).get("what_id", ""),
            },
        )

