from __future__ import annotations

"""Microsoft Graph client for Teams meeting recordings (no MediaAsset creation)."""

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
from turing.connectors.teams.serializers import (
    TeamsRecording,
    normalize_meeting_recordings,
)

logger = logging.getLogger(__name__)

DEFAULT_GRAPH_BASE = "https://graph.microsoft.com/v1.0/"


class TeamsClient:
    """
    Isolated Microsoft Graph HTTP client for Teams recordings.

    Authenticates with a Bearer access token. Does not create MediaAssets.
    Never logs access tokens / Authorization headers.
    """

    def __init__(
        self,
        *,
        api_token: str,
        base_url: str = DEFAULT_GRAPH_BASE,
        timeout_seconds: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        api_token = (api_token or "").strip()
        if not api_token:
            raise ConnectorConfigurationError("Teams access token is required.")
        self._api_token = api_token
        self.base_url = base_url if base_url.endswith("/") else f"{base_url}/"
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_token}",
            "Content-Type": "application/json",
            "User-Agent": "turing-teams-connector/1.0",
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
            logger.warning("Teams Graph request failed for %s %s", method, path)
            raise TemporaryConnectorError(
                f"Teams Graph request failed: {exc}"
            ) from exc

        if response.status_code >= 400:
            logger.warning(
                "Teams Graph error status=%s path=%s",
                response.status_code,
                path,
            )
            if response.status_code in {401, 403}:
                raise AuthenticationError(
                    f"Teams Graph authentication failed (HTTP {response.status_code})."
                )
            if response.status_code == 429 or response.status_code >= 500:
                raise TemporaryConnectorError(
                    f"Teams Graph temporary error (HTTP {response.status_code})."
                )
            raise ConnectorHealthError(
                f"Teams Graph returned HTTP {response.status_code} for {path}."
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise ConnectorHealthError("Teams Graph returned invalid JSON.") from exc

    def authenticate(self) -> dict[str, Any]:
        """Validate credentials with ``GET /me`` (never returns the token)."""
        return self._request("GET", "me")

    def health_check(self) -> dict[str, Any]:
        payload = self.authenticate()
        display = str(
            payload.get("displayName")
            or payload.get("userPrincipalName")
            or payload.get("mail")
            or ""
        )
        return {
            "ok": True,
            "account_name": display,
            "user_id": str(payload.get("id") or ""),
        }

    def list_online_meetings(self, *, top: int = 50) -> list[dict[str, Any]]:
        """List recent online meetings for the authorized user."""
        payload = self._request(
            "GET",
            "me/onlineMeetings",
            params={"$top": max(1, min(int(top), 100))},
        )
        values = payload.get("value") if isinstance(payload, dict) else None
        if not isinstance(values, list):
            return []
        return [v for v in values if isinstance(v, dict)]

    def fetch_recording_metadata(self, meeting_id: str) -> list[TeamsRecording]:
        """Fetch recordings for a single online meeting id."""
        meeting_id = (meeting_id or "").strip()
        if not meeting_id:
            raise ConnectorConfigurationError("meeting_id is required.")
        payload = self._request(
            "GET",
            f"me/onlineMeetings/{meeting_id}/recordings",
        )
        return normalize_meeting_recordings(
            payload if isinstance(payload, dict) else {},
            meeting_id=meeting_id,
        )

    def list_recordings(
        self,
        *,
        from_date: str | None = None,
        to_date: str | None = None,
        page_size: int = 50,
    ) -> list[TeamsRecording]:
        """
        Discover meeting recordings for the authorized user.

        Lists online meetings then fetches recordings per meeting. ``from_date`` /
        ``to_date`` are reserved for future Graph filters (currently unused).
        """
        _ = (from_date, to_date)  # reserved
        meetings = self.list_online_meetings(top=page_size)
        if not meetings:
            # Some tenants expose a flat recordings feed; try a normalized empty.
            return []

        out: list[TeamsRecording] = []
        for meeting in meetings:
            meeting_id = str(meeting.get("id") or "").strip()
            topic = str(meeting.get("subject") or meeting.get("topic") or "").strip()
            if not meeting_id:
                continue
            if isinstance(meeting.get("recordings"), list):
                out.extend(
                    normalize_meeting_recordings(
                        meeting,
                        meeting_id=meeting_id,
                        topic=topic,
                    )
                )
                continue
            try:
                for recording in self.fetch_recording_metadata(meeting_id):
                    if topic and not recording.topic:
                        out.append(
                            TeamsRecording(
                                recording_id=recording.recording_id,
                                meeting_id=recording.meeting_id,
                                topic=topic,
                                download_url=recording.download_url,
                                file_type=recording.file_type,
                                file_extension=recording.file_extension,
                                file_size=recording.file_size,
                                recording_start=recording.recording_start,
                                recording_end=recording.recording_end,
                                metadata={**recording.metadata, "topic": topic},
                            )
                        )
                    else:
                        out.append(recording)
            except (TemporaryConnectorError, AuthenticationError):
                raise
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Skipping Teams meeting recordings meeting_id=%s",
                    meeting_id,
                )
                continue
        return out
