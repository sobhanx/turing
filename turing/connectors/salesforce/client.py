from __future__ import annotations

"""Salesforce REST / SOQL client (no MediaAsset creation)."""

import logging
from typing import Any
from urllib.parse import quote, urljoin

import requests

from turing.connectors.exceptions import (
    AuthenticationError,
    ConnectorConfigurationError,
    ConnectorHealthError,
    TemporaryConnectorError,
)
from turing.connectors.salesforce.serializers import (
    SalesforceRecording,
    normalize_query_records,
)

logger = logging.getLogger(__name__)

# Prefer VoiceCall when available; fall back to Task call activities with a URL field.
DEFAULT_RECORDINGS_SOQL = (
    "SELECT Id, Name, CreatedDate, FromPhoneNumber, ToPhoneNumber, "
    "CallStartDateTime, CallEndDateTime, RecordingUrl "
    "FROM VoiceCall "
    "WHERE RecordingUrl != null "
    "ORDER BY CreatedDate DESC "
    "LIMIT 50"
)

FALLBACK_TASK_SOQL = (
    "SELECT Id, Subject, CreatedDate, CallType, WhoId, WhatId, OwnerId, "
    "Recording_Link__c "
    "FROM Task "
    "WHERE CallType != null AND Recording_Link__c != null "
    "ORDER BY CreatedDate DESC "
    "LIMIT 50"
)


class SalesforceClient:
    """
    Isolated Salesforce REST client for CRM call/meeting recordings.

    Uses Bearer access token against the org ``instance_url``.
    Does not create MediaAssets. Never logs tokens.
    """

    def __init__(
        self,
        *,
        api_token: str,
        instance_url: str,
        api_version: str = "v59.0",
        timeout_seconds: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        api_token = (api_token or "").strip()
        instance_url = (instance_url or "").strip().rstrip("/")
        if not api_token:
            raise ConnectorConfigurationError("Salesforce access token is required.")
        if not instance_url:
            raise ConnectorConfigurationError(
                "Salesforce instance_url is required "
                "(from OAuth token response or installation config)."
            )
        self._api_token = api_token
        self.instance_url = instance_url
        self.api_version = api_version if api_version.startswith("v") else f"v{api_version}"
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()
        self.base_url = f"{self.instance_url}/services/data/{self.api_version}/"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_token}",
            "Content-Type": "application/json",
            "User-Agent": "turing-salesforce-connector/1.0",
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
            logger.warning("Salesforce API request failed for %s %s", method, path)
            raise TemporaryConnectorError(
                f"Salesforce API request failed: {exc}"
            ) from exc

        if response.status_code >= 400:
            logger.warning(
                "Salesforce API error status=%s path=%s",
                response.status_code,
                path,
            )
            if response.status_code in {401, 403}:
                raise AuthenticationError(
                    f"Salesforce authentication failed "
                    f"(HTTP {response.status_code})."
                )
            if response.status_code == 429 or response.status_code >= 500:
                raise TemporaryConnectorError(
                    f"Salesforce temporary error (HTTP {response.status_code})."
                )
            raise ConnectorHealthError(
                f"Salesforce API returned HTTP {response.status_code} for {path}."
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise ConnectorHealthError(
                "Salesforce API returned invalid JSON."
            ) from exc

    def authenticate(self) -> dict[str, Any]:
        """Validate credentials with identity / userinfo probe."""
        return self._request("GET", "chatter/users/me")

    def health_check(self) -> dict[str, Any]:
        payload = self.authenticate()
        return {
            "ok": True,
            "account_name": str(
                payload.get("displayName")
                or payload.get("name")
                or payload.get("username")
                or ""
            ),
            "user_id": str(payload.get("id") or ""),
            "instance_url": self.instance_url,
        }

    def query(self, soql: str) -> dict[str, Any]:
        """Run a SOQL query. Never log the access token."""
        soql = (soql or "").strip()
        if not soql:
            raise ConnectorConfigurationError("SOQL query is required.")
        return self._request("GET", f"query?q={quote(soql)}")

    def list_recordings(
        self,
        *,
        from_date: str | None = None,
        to_date: str | None = None,
        soql: str | None = None,
    ) -> list[SalesforceRecording]:
        """
        Discover CRM records that reference call/meeting recordings.

        Tries VoiceCall first, then Task fallback when VoiceCall is unavailable.
        """
        _ = (from_date, to_date)
        if soql:
            payload = self.query(soql)
            return normalize_query_records(payload if isinstance(payload, dict) else {})

        try:
            payload = self.query(DEFAULT_RECORDINGS_SOQL)
            return normalize_query_records(payload if isinstance(payload, dict) else {})
        except ConnectorHealthError:
            # Object may not exist in this org — try Task-based recordings.
            logger.info("Salesforce VoiceCall query unavailable; trying Task fallback")
            payload = self.query(FALLBACK_TASK_SOQL)
            return normalize_query_records(payload if isinstance(payload, dict) else {})

    def fetch_recording_metadata(self, record_id: str) -> list[SalesforceRecording]:
        """Fetch a single sObject by id (VoiceCall-shaped fields)."""
        record_id = (record_id or "").strip()
        if not record_id:
            raise ConnectorConfigurationError("record_id is required.")
        payload = self._request(
            "GET",
            f"sobjects/VoiceCall/{record_id}",
        )
        item = normalize_query_records(
            {"records": [payload]} if isinstance(payload, dict) else {}
        )
        return item
