from __future__ import annotations

"""Microsoft identity OAuth2 helpers for Teams (never log tokens)."""

import logging
from typing import Any
from urllib.parse import urlencode

import requests

from turing.connectors.exceptions import ConnectorConfigurationError, ConnectorError

logger = logging.getLogger(__name__)

DEFAULT_AUTHORIZE_URL = (
    "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
)
DEFAULT_TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
DEFAULT_REVOKE_URL = "https://graph.microsoft.com/v1.0/me/revokeSignInSessions"
DEFAULT_SCOPES = (
    "openid offline_access User.Read OnlineMeetings.Read "
    "OnlineMeetingRecording.Read.All"
)


class TeamsOAuthClient:
    """
    Microsoft identity platform OAuth client for Teams / Graph.

    Uses client_id / client_secret in the token form body (confidential client).
    Never logs token payloads.
    """

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        authorize_url: str = DEFAULT_AUTHORIZE_URL,
        token_url: str = DEFAULT_TOKEN_URL,
        revoke_url: str = DEFAULT_REVOKE_URL,
        timeout_seconds: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        self.client_id = (client_id or "").strip()
        self.client_secret = (client_secret or "").strip()
        if not self.client_id or not self.client_secret:
            raise ConnectorConfigurationError(
                "Teams OAuth is not configured. Set TURING_TEAMS_CLIENT_ID and "
                "TURING_TEAMS_CLIENT_SECRET."
            )
        self.authorize_url = (authorize_url or DEFAULT_AUTHORIZE_URL).rstrip("?")
        self.token_url = (token_url or DEFAULT_TOKEN_URL).strip() or DEFAULT_TOKEN_URL
        self.revoke_url = (revoke_url or DEFAULT_REVOKE_URL).strip() or DEFAULT_REVOKE_URL
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()

    def build_authorization_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        scopes: str = DEFAULT_SCOPES,
    ) -> str:
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "response_mode": "query",
            "state": state,
            "scope": scopes or DEFAULT_SCOPES,
        }
        return f"{self.authorize_url}?{urlencode(params)}"

    def exchange_code(self, code: str, *, redirect_uri: str) -> dict[str, Any]:
        code = (code or "").strip()
        if not code:
            raise ConnectorConfigurationError("OAuth authorization code is required.")
        return self._token_request(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            }
        )

    def refresh_token(self, refresh_token: str) -> dict[str, Any]:
        refresh_token = (refresh_token or "").strip()
        if not refresh_token:
            raise ConnectorConfigurationError("OAuth refresh token is required.")
        return self._token_request(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            }
        )

    def revoke_token(self, access_token: str) -> None:
        """Best-effort Graph session revoke. Failures logged without token material."""
        access_token = (access_token or "").strip()
        if not access_token:
            return
        try:
            response = self.session.post(
                self.revoke_url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                timeout=self.timeout_seconds,
            )
            if response.status_code >= 400:
                logger.warning(
                    "Teams OAuth revoke failed status=%s",
                    response.status_code,
                )
        except requests.RequestException:
            logger.warning("Teams OAuth revoke request failed", exc_info=True)

    def _token_request(self, data: dict[str, str]) -> dict[str, Any]:
        try:
            response = self.session.post(
                self.token_url,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data=data,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            logger.warning("Teams OAuth token request failed")
            raise ConnectorError(
                "Teams OAuth token request failed.",
                code="teams_oauth_token_failed",
            ) from exc

        if response.status_code >= 400:
            logger.warning(
                "Teams OAuth token endpoint status=%s",
                response.status_code,
            )
            raise ConnectorError(
                f"Teams OAuth token exchange failed (HTTP {response.status_code}).",
                code="teams_oauth_token_failed",
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise ConnectorError(
                "Teams OAuth token response was not JSON.",
                code="teams_oauth_token_failed",
            ) from exc

        if not isinstance(payload, dict) or not payload.get("access_token"):
            raise ConnectorError(
                "Teams OAuth token response missing access_token.",
                code="teams_oauth_token_failed",
            )
        return payload
