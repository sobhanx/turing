from __future__ import annotations

"""Salesforce OAuth2 helpers (never log tokens)."""

import logging
from typing import Any
from urllib.parse import urlencode

import requests

from turing.connectors.exceptions import ConnectorConfigurationError, ConnectorError

logger = logging.getLogger(__name__)

DEFAULT_AUTHORIZE_URL = "https://login.salesforce.com/services/oauth2/authorize"
DEFAULT_TOKEN_URL = "https://login.salesforce.com/services/oauth2/token"
DEFAULT_REVOKE_URL = "https://login.salesforce.com/services/oauth2/revoke"
DEFAULT_SCOPES = "api refresh_token offline_access"


class SalesforceOAuthClient:
    """
    Salesforce connected-app OAuth client.

    Token responses include ``instance_url`` (required for API calls).
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
                "Salesforce OAuth is not configured. Set "
                "TURING_SALESFORCE_CLIENT_ID and TURING_SALESFORCE_CLIENT_SECRET."
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
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
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
                "grant_type": "authorization_code",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": redirect_uri,
                "code": code,
            }
        )

    def refresh_token(self, refresh_token: str) -> dict[str, Any]:
        refresh_token = (refresh_token or "").strip()
        if not refresh_token:
            raise ConnectorConfigurationError("OAuth refresh token is required.")
        return self._token_request(
            {
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": refresh_token,
            }
        )

    def revoke_token(self, token: str) -> None:
        token = (token or "").strip()
        if not token:
            return
        try:
            response = self.session.post(
                self.revoke_url,
                data={"token": token},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=self.timeout_seconds,
            )
            if response.status_code >= 400:
                logger.warning(
                    "Salesforce OAuth revoke failed status=%s",
                    response.status_code,
                )
        except requests.RequestException:
            logger.warning("Salesforce OAuth revoke request failed", exc_info=True)

    def _token_request(self, data: dict[str, str]) -> dict[str, Any]:
        try:
            response = self.session.post(
                self.token_url,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data=data,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            logger.warning("Salesforce OAuth token request failed")
            raise ConnectorError(
                "Salesforce OAuth token request failed.",
                code="salesforce_oauth_token_failed",
            ) from exc

        if response.status_code >= 400:
            logger.warning(
                "Salesforce OAuth token endpoint status=%s",
                response.status_code,
            )
            raise ConnectorError(
                f"Salesforce OAuth token exchange failed "
                f"(HTTP {response.status_code}).",
                code="salesforce_oauth_token_failed",
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise ConnectorError(
                "Salesforce OAuth token response was not JSON.",
                code="salesforce_oauth_token_failed",
            ) from exc

        if not isinstance(payload, dict) or not payload.get("access_token"):
            raise ConnectorError(
                "Salesforce OAuth token response missing access_token.",
                code="salesforce_oauth_token_failed",
            )
        return payload
