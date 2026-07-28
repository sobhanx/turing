from __future__ import annotations

"""Zoom OAuth2 HTTP helpers (authorize / token / revoke). Never log tokens."""

import base64
import logging
from typing import Any
from urllib.parse import urlencode

import requests

from turing.connectors.exceptions import ConnectorConfigurationError, ConnectorError

logger = logging.getLogger(__name__)

DEFAULT_AUTHORIZE_URL = "https://zoom.us/oauth/authorize"
DEFAULT_TOKEN_URL = "https://zoom.us/oauth/token"
DEFAULT_REVOKE_URL = "https://zoom.us/oauth/revoke"
DEFAULT_SCOPES = "recording:read user:read:user"


class ZoomOAuthClient:
    """
    Zoom OAuth application client (Server-to-Server style user OAuth).

    Uses client_id / client_secret from settings. Never logs token payloads.
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
                "Zoom OAuth is not configured. Set TURING_ZOOM_CLIENT_ID and "
                "TURING_ZOOM_CLIENT_SECRET."
            )
        self.authorize_url = (authorize_url or DEFAULT_AUTHORIZE_URL).rstrip("?")
        self.token_url = (token_url or DEFAULT_TOKEN_URL).strip() or DEFAULT_TOKEN_URL
        self.revoke_url = (revoke_url or DEFAULT_REVOKE_URL).strip() or DEFAULT_REVOKE_URL
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()

    def _basic_auth_header(self) -> str:
        raw = f"{self.client_id}:{self.client_secret}".encode("utf-8")
        return "Basic " + base64.b64encode(raw).decode("ascii")

    def build_authorization_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        scopes: str = DEFAULT_SCOPES,
    ) -> str:
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "state": state,
        }
        if scopes:
            params["scope"] = scopes
        return f"{self.authorize_url}?{urlencode(params)}"

    def exchange_code(self, code: str, *, redirect_uri: str) -> dict[str, Any]:
        """Exchange authorization code for tokens. Does not log the response body."""
        code = (code or "").strip()
        if not code:
            raise ConnectorConfigurationError("OAuth authorization code is required.")
        return self._token_request(
            {
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
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            }
        )

    def revoke_token(self, token: str) -> None:
        """Best-effort remote revoke. Failures are logged without token material."""
        token = (token or "").strip()
        if not token:
            return
        try:
            response = self.session.post(
                self.revoke_url,
                headers={
                    "Authorization": self._basic_auth_header(),
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={"token": token},
                timeout=self.timeout_seconds,
            )
            if response.status_code >= 400:
                logger.warning(
                    "Zoom OAuth revoke failed status=%s",
                    response.status_code,
                )
        except requests.RequestException:
            logger.warning("Zoom OAuth revoke request failed", exc_info=True)

    def _token_request(self, data: dict[str, str]) -> dict[str, Any]:
        try:
            response = self.session.post(
                self.token_url,
                headers={
                    "Authorization": self._basic_auth_header(),
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data=data,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            logger.warning("Zoom OAuth token request failed")
            raise ConnectorError(
                "Zoom OAuth token request failed.",
                code="zoom_oauth_token_failed",
            ) from exc

        if response.status_code >= 400:
            # Never log response body (may include error details with codes).
            logger.warning(
                "Zoom OAuth token endpoint status=%s",
                response.status_code,
            )
            raise ConnectorError(
                f"Zoom OAuth token exchange failed (HTTP {response.status_code}).",
                code="zoom_oauth_token_failed",
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise ConnectorError(
                "Zoom OAuth token response was not JSON.",
                code="zoom_oauth_token_failed",
            ) from exc

        if not isinstance(payload, dict) or not payload.get("access_token"):
            raise ConnectorError(
                "Zoom OAuth token response missing access_token.",
                code="zoom_oauth_token_failed",
            )
        return payload
