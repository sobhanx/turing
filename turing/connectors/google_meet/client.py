from __future__ import annotations

"""Google Drive / Meet recording client (no MediaAsset creation)."""

import logging
from typing import Any
from urllib.parse import urljoin

import requests

from turing.connectors.exceptions import (
    AuthenticationError,
    ConnectorConfigurationError,
    ConnectorHealthError,
    TemporaryConnectorError,
)
from turing.connectors.google_meet.serializers import (
    GoogleMeetRecording,
    normalize_meeting_recordings,
)

logger = logging.getLogger(__name__)

DEFAULT_DRIVE_BASE = "https://www.googleapis.com/drive/v3/"
DEFAULT_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

# Drive query for Meet-produced recordings (folder name / description heuristics).
_DEFAULT_RECORDINGS_QUERY = (
    "(name contains 'Meet Recording' or name contains 'Recording' "
    "or mimeType contains 'video/' or mimeType contains 'audio/') "
    "and trashed = false"
)


class GoogleMeetClient:
    """
    Isolated Google Drive HTTP client for Meet recordings.

    Authenticates with a Bearer access token. Does not create MediaAssets.
    Never logs access tokens / Authorization headers.
    """

    def __init__(
        self,
        *,
        api_token: str,
        base_url: str = DEFAULT_DRIVE_BASE,
        timeout_seconds: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        api_token = (api_token or "").strip()
        if not api_token:
            raise ConnectorConfigurationError("Google Meet access token is required.")
        self._api_token = api_token
        self.base_url = base_url if base_url.endswith("/") else f"{base_url}/"
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_token}",
            "Content-Type": "application/json",
            "User-Agent": "turing-google-meet-connector/1.0",
        }

    def _request(self, method: str, url_or_path: str, **kwargs: Any) -> Any:
        if url_or_path.startswith("http://") or url_or_path.startswith("https://"):
            url = url_or_path
        else:
            url = urljoin(self.base_url, url_or_path.lstrip("/"))
        try:
            response = self.session.request(
                method,
                url,
                headers=self._headers(),
                timeout=self.timeout_seconds,
                **kwargs,
            )
        except requests.RequestException as exc:
            logger.warning("Google Meet API request failed for %s", method)
            raise TemporaryConnectorError(
                f"Google Meet API request failed: {exc}"
            ) from exc

        if response.status_code >= 400:
            logger.warning(
                "Google Meet API error status=%s",
                response.status_code,
            )
            if response.status_code in {401, 403}:
                raise AuthenticationError(
                    f"Google Meet authentication failed "
                    f"(HTTP {response.status_code})."
                )
            if response.status_code == 429 or response.status_code >= 500:
                raise TemporaryConnectorError(
                    f"Google Meet temporary error (HTTP {response.status_code})."
                )
            raise ConnectorHealthError(
                f"Google Meet API returned HTTP {response.status_code}."
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise ConnectorHealthError(
                "Google Meet API returned invalid JSON."
            ) from exc

    def authenticate(self) -> dict[str, Any]:
        """Validate credentials via userinfo (never returns the token)."""
        return self._request("GET", DEFAULT_USERINFO_URL)

    def health_check(self) -> dict[str, Any]:
        payload = self.authenticate()
        return {
            "ok": True,
            "account_name": str(
                payload.get("name") or payload.get("email") or ""
            ),
            "user_id": str(payload.get("sub") or payload.get("id") or ""),
        }

    def fetch_recording_metadata(self, file_id: str) -> list[GoogleMeetRecording]:
        """Fetch a single Drive file as a recording descriptor."""
        file_id = (file_id or "").strip()
        if not file_id:
            raise ConnectorConfigurationError("file_id is required.")
        payload = self._request(
            "GET",
            f"files/{file_id}",
            params={
                "fields": (
                    "id,name,mimeType,size,createdTime,webContentLink,"
                    "webViewLink,appProperties"
                ),
            },
        )
        return normalize_meeting_recordings(
            payload if isinstance(payload, dict) else {}
        )

    def list_recordings(
        self,
        *,
        from_date: str | None = None,
        to_date: str | None = None,
        page_size: int = 50,
        query: str | None = None,
    ) -> list[GoogleMeetRecording]:
        """
        Discover Meet recordings via Drive ``files.list``.

        ``from_date`` / ``to_date`` reserved for future createdTime filters.
        """
        _ = (from_date, to_date)
        params: dict[str, Any] = {
            "pageSize": max(1, min(int(page_size), 100)),
            "q": (query or _DEFAULT_RECORDINGS_QUERY).strip(),
            "fields": (
                "files(id,name,mimeType,size,createdTime,webContentLink,"
                "webViewLink,appProperties)"
            ),
            "orderBy": "createdTime desc",
        }
        payload = self._request("GET", "files", params=params)
        return normalize_meeting_recordings(
            payload if isinstance(payload, dict) else {}
        )
