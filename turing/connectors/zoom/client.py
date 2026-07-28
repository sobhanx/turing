from __future__ import annotations

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
from turing.connectors.zoom.serializers import (
    ZoomRecording,
    normalize_meeting_recordings,
    normalize_recordings_list,
)

logger = logging.getLogger(__name__)

DEFAULT_ZOOM_API_BASE = "https://api.zoom.us/v2/"


class ZoomClient:
    """
    Isolated Zoom HTTP client.

    Authenticates with a Bearer access token (OAuth). Does not create MediaAssets.
    Never logs access tokens / Authorization headers.
    """

    def __init__(
        self,
        *,
        api_token: str,
        account_id: str = "",
        base_url: str = DEFAULT_ZOOM_API_BASE,
        timeout_seconds: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        api_token = (api_token or "").strip()
        if not api_token:
            raise ConnectorConfigurationError("Zoom access token is required.")
        self.account_id = (account_id or "").strip()
        self._api_token = api_token
        self.base_url = base_url if base_url.endswith("/") else f"{base_url}/"
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_token}",
            "Content-Type": "application/json",
            "User-Agent": "turing-zoom-connector/1.0",
        }

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = urljoin(self.base_url, path.lstrip("/"))
        try:
            response = self.session.request(
                method,
                url,
                headers=self._headers(),
                timeout=self.timeout_seconds,
                **kwargs,
            )
        except requests.RequestException as exc:
            logger.warning("Zoom API request failed for %s %s", method, path)
            raise ConnectorHealthError(f"Zoom API request failed: {exc}") from exc

        if response.status_code >= 400:
            logger.warning(
                "Zoom API error status=%s path=%s",
                response.status_code,
                path,
            )
            if response.status_code in {401, 403}:
                raise AuthenticationError(
                    f"Zoom API authentication failed (HTTP {response.status_code})."
                )
            if response.status_code == 429 or response.status_code >= 500:
                raise TemporaryConnectorError(
                    f"Zoom API temporary error (HTTP {response.status_code})."
                )
            raise ConnectorHealthError(
                f"Zoom API returned HTTP {response.status_code} for {path}."
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise ConnectorHealthError("Zoom API returned invalid JSON.") from exc

    def authenticate(self) -> dict[str, Any]:
        """Validate credentials with a lightweight probe (never returns the token)."""
        if self.account_id:
            return self._request("GET", f"accounts/{self.account_id}")
        return self._request("GET", "users/me")

    def health_check(self) -> dict[str, Any]:
        payload = self.authenticate()
        return {
            "ok": True,
            "account_id": self.account_id,
            "account_name": str(
                payload.get("account_name")
                or payload.get("display_name")
                or payload.get("first_name")
                or payload.get("name")
                or ""
            ),
        }

    def list_recordings(
        self,
        *,
        from_date: str | None = None,
        to_date: str | None = None,
        page_size: int = 30,
    ) -> list[ZoomRecording]:
        """List cloud recordings for the authorized user (``/users/me/recordings``)."""
        params: dict[str, Any] = {"page_size": page_size}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        payload = self._request("GET", "users/me/recordings", params=params)
        return normalize_recordings_list(payload if isinstance(payload, dict) else {})

    def fetch_recording_metadata(self, meeting_id: str) -> list[ZoomRecording]:
        """Fetch recording files for a single meeting."""
        meeting_id = (meeting_id or "").strip()
        if not meeting_id:
            raise ConnectorConfigurationError("meeting_id is required.")
        payload = self._request("GET", f"meetings/{meeting_id}/recordings")
        return normalize_meeting_recordings(payload if isinstance(payload, dict) else {})
