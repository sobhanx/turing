from __future__ import annotations

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
from turing.connectors.zoom.client import ZoomClient
from turing.connectors.zoom.oauth import DEFAULT_SCOPES, ZoomOAuthClient
from turing.connectors.zoom.serializers import pick_primary_recording
from turing.domain.enums import ConnectorAuthType, UseCase
from turing.services.connector_installation import ConnectorInstallationService
from turing.services.external_reference import ExternalReferenceService
from turing.services.media import MediaService
from turing.services.oauth_state import OAuthStateService

logger = logging.getLogger(__name__)

EXTERNAL_SYSTEM = "zoom"
EXTERNAL_TYPE = "meeting"
_TOKEN_SKEW = timedelta(seconds=60)


def _zoom_oauth_settings() -> dict[str, str]:
    return {
        "client_id": str(getattr(settings, "TURING_ZOOM_CLIENT_ID", "") or ""),
        "client_secret": str(getattr(settings, "TURING_ZOOM_CLIENT_SECRET", "") or ""),
        "authorize_url": str(
            getattr(settings, "TURING_ZOOM_OAUTH_AUTHORIZE_URL", "") or ""
        ),
        "token_url": str(getattr(settings, "TURING_ZOOM_OAUTH_TOKEN_URL", "") or ""),
        "revoke_url": str(getattr(settings, "TURING_ZOOM_OAUTH_REVOKE_URL", "") or ""),
        "redirect_uri": str(
            getattr(settings, "TURING_ZOOM_OAUTH_REDIRECT_URI", "") or ""
        ),
        "scopes": str(getattr(settings, "TURING_ZOOM_OAUTH_SCOPES", "") or "")
        or DEFAULT_SCOPES,
    }


class ZoomConnector(BaseConnector):
    """
    Zoom Cloud Recording → Turing media connector (OAuth2).

    Tokens live on ``ConnectorCredential`` (encrypted). Sync refreshes expired
    access tokens automatically via the refresh token.
    """

    connector_type = "zoom"
    display_name = "Zoom"
    description = "Sync Zoom cloud meeting recordings into Turing for transcription."
    provider = "Zoom"
    category = ConnectorCategory.MEETINGS
    documentation_url = (
        "https://developers.zoom.us/docs/api/rest/reference/zoom-api/"
        "methods/#tag/Cloud-Recording"
    )
    auth_type = ConnectorAuthType.OAUTH2
    supports_oauth = True
    supports_refresh = True
    supports_revoke = True
    supported_sync_types = ("media",)
    required_scopes = split_scopes(DEFAULT_SCOPES)
    installation_requirements = InstallationRequirements(
        oauth_scopes=split_scopes(DEFAULT_SCOPES),
        messages=(
            "Configure Zoom OAuth client settings on the host (TURING_ZOOM_*).",
            "Set TURING_ZOOM_OAUTH_REDIRECT_URI to the Turing OAuth callback.",
            "Complete OAuth authorization after creating the installation.",
        ),
    )

    def __init__(
        self,
        installation,
        *,
        client: ZoomClient | None = None,
        oauth_client: ZoomOAuthClient | None = None,
    ) -> None:
        super().__init__(installation)
        self._client = client
        self._oauth_client = oauth_client

    @property
    def name(self) -> str:
        return "zoom"

    def _oauth(self) -> ZoomOAuthClient:
        if self._oauth_client is not None:
            return self._oauth_client
        cfg = _zoom_oauth_settings()
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
        return ZoomOAuthClient(**kwargs)

    def _redirect_uri(self, redirect_uri: str | None = None) -> str:
        uri = (redirect_uri or "").strip() or _zoom_oauth_settings()["redirect_uri"]
        if not uri:
            raise ConnectorConfigurationError(
                "Zoom OAuth redirect URI is not configured. "
                "Set TURING_ZOOM_OAUTH_REDIRECT_URI."
            )
        return uri

    def validate_config(self) -> None:
        """Require Zoom OAuth app settings (not per-installation api_token)."""
        cfg = _zoom_oauth_settings()
        if not cfg["client_id"] or not cfg["client_secret"]:
            raise ConnectorConfigurationError(
                "Zoom OAuth is not configured. Set TURING_ZOOM_CLIENT_ID and "
                "TURING_ZOOM_CLIENT_SECRET."
            )

    def validate_credentials(self) -> None:
        self.validate_config()
        super().validate_credentials()

    def authorization_url(self, *, redirect_uri: str | None = None, state: str | None = None) -> str:
        """Build the Zoom authorize URL for this installation."""
        self.validate_config()
        redirect = self._redirect_uri(redirect_uri)
        state_value = state or OAuthStateService().generate(
            installation_id=str(self.installation.id),
            organization_id=self.installation.organization_id,
            connector_type=self.connector_type,
        )
        scopes = _zoom_oauth_settings()["scopes"]
        return self._oauth().build_authorization_url(
            redirect_uri=redirect,
            state=state_value,
            scopes=scopes,
        )

    def exchange_code(self, code: str, *, redirect_uri: str | None = None) -> None:
        """
        Exchange an authorization code, encrypt tokens, and store credentials.

        Does not activate the installation — caller should use
        ``ConnectorInstallationService.activate()``.
        """
        self.validate_config()
        redirect = self._redirect_uri(redirect_uri)
        payload = self._oauth().exchange_code(code, redirect_uri=redirect)
        self._persist_token_payload(payload)

    def refresh_credentials(self) -> None:
        """Refresh access token using the stored refresh token."""
        self.validate_config()
        refresh = self._decrypt_refresh_token()
        if not refresh:
            ConnectorInstallationService().expire(self.installation)
            raise AuthenticationError("Zoom refresh token is missing.")
        try:
            payload = self._oauth().refresh_token(refresh)
        except ConnectorError as exc:
            ConnectorInstallationService().expire(self.installation)
            raise AuthenticationError(
                "Zoom OAuth token refresh failed.",
                code=getattr(exc, "code", "zoom_oauth_refresh_failed"),
            ) from exc
        self._persist_token_payload(payload)

    def revoke_credentials(self) -> None:
        """Best-effort Zoom token revoke (local clear handled by installation service)."""
        try:
            token = self._decrypt_access_token() or self._decrypt_refresh_token()
            if token:
                self._oauth().revoke_token(token)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Zoom revoke_credentials failed installation_id=%s",
                getattr(self.installation, "id", None),
            )

    def ensure_fresh_credentials(self) -> None:
        """
        Refresh access token when expired (or within skew), then validate.

        On refresh failure the installation is marked expired.
        """
        self.validate_config()
        cred = self._credential()
        if cred is None or not cred.has_access_token():
            raise ConnectorConfigurationError(
                "OAuth access token is missing. Complete authorization first."
            )
        expires_at = cred.expires_at
        if expires_at is not None and expires_at <= timezone.now() + _TOKEN_SKEW:
            logger.info(
                "Refreshing Zoom OAuth token installation_id=%s",
                self.installation.id,
            )
            try:
                self.refresh_credentials()
            except AuthenticationError:
                raise
            except ConnectorError as exc:
                ConnectorInstallationService().expire(self.installation)
                raise AuthenticationError(
                    "Zoom OAuth token refresh failed.",
                    code=getattr(exc, "code", "zoom_oauth_refresh_failed"),
                ) from exc
        self.validate_credentials()

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

        meta = {
            "scope": str(payload.get("scope") or ""),
            "token_type": str(payload.get("token_type") or ""),
        }
        # Drop empty metadata keys; never store raw token fields.
        meta = {k: v for k, v in meta.items() if v}

        ConnectorInstallationService().store_credentials(
            self.installation,
            access_token=access,
            refresh_token=refresh,
            expires_at=expires_at,
            auth_type=ConnectorAuthType.OAUTH2,
            metadata=meta,
        )

    def _build_client(self) -> ZoomClient:
        if self._client is not None:
            return self._client
        token = self._decrypt_access_token()
        if not token:
            raise ConnectorConfigurationError(
                "OAuth access token is missing. Complete authorization first."
            )
        return ZoomClient(
            api_token=token,
            account_id=str(self.config.get("account_id") or ""),
            base_url=str(self.config.get("base_url") or "") or "https://api.zoom.us/v2/",
        )

    def health_check(self) -> dict[str, Any]:
        self.ensure_fresh_credentials()
        try:
            result = self._build_client().health_check()
        except ConnectorError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ConnectorHealthError(f"Zoom health check failed: {exc}") from exc
        return {
            "ok": bool(result.get("ok")),
            "account_id": self.config.get("account_id") or "",
            "account_name": result.get("account_name") or "",
        }

    def pull_media(self, **kwargs: Any) -> list[MediaPullItem]:
        self.ensure_fresh_credentials()
        client = self._build_client()
        recordings = client.list_recordings(
            from_date=kwargs.get("from_date"),
            to_date=kwargs.get("to_date"),
        )
        by_meeting: dict[str, list] = {}
        for recording in recordings:
            by_meeting.setdefault(recording.meeting_id, []).append(recording)

        items: list[MediaPullItem] = []
        for meeting_id, group in by_meeting.items():
            primary = pick_primary_recording(group)
            if primary is None:
                continue
            ext = primary.file_extension or "mp4"
            filename = f"zoom-{primary.recording_id}.{ext}"
            items.append(
                MediaPullItem(
                    external_id=primary.recording_id,
                    source_url=primary.download_url,
                    filename=filename,
                    metadata={
                        "external_system": EXTERNAL_SYSTEM,
                        "external_type": EXTERNAL_TYPE,
                        "external_id": primary.recording_id,
                        "media_url": primary.download_url,
                        "meeting_id": meeting_id,
                        "topic": primary.topic,
                        "file_type": primary.file_type,
                        "file_size": primary.file_size,
                        "recording_start": primary.recording_start,
                        "recording_end": primary.recording_end,
                        **dict(primary.metadata or {}),
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
            raise ConnectorSyncError(f"Zoom pull_media failed: {exc}") from exc

        org = self.installation.organization
        media_service = MediaService()
        refs = ExternalReferenceService()
        created_items: list[MediaPullItem] = []
        skipped = 0

        for item in items:
            if not item.source_url:
                skipped += 1
                continue
            existing = refs.lookup(
                organization=org,
                external_system=EXTERNAL_SYSTEM,
                external_type=EXTERNAL_TYPE,
                external_id=item.external_id,
            )
            if existing.filter(media__isnull=False).exists():
                skipped += 1
                continue
            try:
                asset = media_service.create_from_url(
                    url=item.source_url,
                    use_case=UseCase.MEETING,
                    organization=org,
                    original_filename=item.filename or f"zoom-{item.external_id}.mp4",
                    metadata={
                        "connector": EXTERNAL_SYSTEM,
                        "connector_installation_id": str(self.installation.id),
                        "zoom": {
                            k: v
                            for k, v in (item.metadata or {}).items()
                            if k
                            not in {
                                "api_token",
                                "token",
                                "secret",
                                "access_token",
                                "refresh_token",
                            }
                        },
                    },
                )
                refs.attach_to_media(
                    asset,
                    external_system=EXTERNAL_SYSTEM,
                    external_type=EXTERNAL_TYPE,
                    external_id=item.external_id,
                    metadata={
                        "meeting_id": (item.metadata or {}).get("meeting_id", ""),
                        "topic": (item.metadata or {}).get("topic", ""),
                    },
                )
                created_items.append(item)
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "Zoom sync failed creating media for recording %s",
                    item.external_id,
                )
                raise ConnectorSyncError(
                    f"Failed to ingest Zoom recording '{item.external_id}': {exc}"
                ) from exc

        return ConnectorSyncResult(
            records_processed=len(created_items),
            media_items=created_items,
            details={"skipped": skipped, "discovered": len(items)},
        )
