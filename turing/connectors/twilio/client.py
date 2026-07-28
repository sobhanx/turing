from __future__ import annotations

"""Twilio REST client for call recordings (no MediaAsset creation)."""

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
from turing.connectors.telephony.serializers import TelephonyCall
from turing.connectors.twilio.serializers import (
    EXTERNAL_SYSTEM,
    normalize_twilio_recording,
    pick_primary_recording,
)

logger = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://api.twilio.com"


class TwilioClient:
    """
    Isolated Twilio REST client for call recording discovery.

    Authenticates with HTTP Basic (Account SID + Auth Token).
    Does not create MediaAssets. Never logs auth tokens.
    """

    def __init__(
        self,
        *,
        account_sid: str,
        auth_token: str,
        api_base: str = DEFAULT_API_BASE,
        timeout_seconds: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        account_sid = (account_sid or "").strip()
        auth_token = (auth_token or "").strip()
        if not account_sid:
            raise ConnectorConfigurationError("Twilio account_sid is required.")
        if not auth_token:
            raise ConnectorConfigurationError("Twilio auth_token is required.")
        self.account_sid = account_sid
        self._auth_token = auth_token
        self.api_base = (api_base or DEFAULT_API_BASE).rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()
        self.base_url = (
            f"{self.api_base}/2010-04-01/Accounts/{self.account_sid}/"
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        if path.startswith("http://") or path.startswith("https://"):
            url = path
        else:
            url = urljoin(self.base_url, path.lstrip("/"))
        try:
            response = self.session.request(
                method,
                url,
                auth=(self.account_sid, self._auth_token),
                timeout=self.timeout_seconds,
                **kwargs,
            )
        except requests.RequestException as exc:
            logger.warning("Twilio API request failed for %s", method)
            raise TemporaryConnectorError(
                f"Twilio API request failed: {exc}"
            ) from exc

        if response.status_code >= 400:
            logger.warning(
                "Twilio API error status=%s",
                response.status_code,
            )
            if response.status_code in {401, 403}:
                raise AuthenticationError(
                    f"Twilio authentication failed (HTTP {response.status_code})."
                )
            if response.status_code == 429 or response.status_code >= 500:
                raise TemporaryConnectorError(
                    f"Twilio temporary error (HTTP {response.status_code})."
                )
            raise ConnectorHealthError(
                f"Twilio API returned HTTP {response.status_code}."
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise ConnectorHealthError("Twilio API returned invalid JSON.") from exc

    def authenticate(self) -> dict[str, Any]:
        """Validate credentials by fetching the Account resource."""
        return self._request(
            "GET",
            f"{self.api_base}/2010-04-01/Accounts/{self.account_sid}.json",
        )

    def health_check(self) -> dict[str, Any]:
        payload = self.authenticate()
        return {
            "ok": True,
            "account_sid": str(payload.get("sid") or self.account_sid),
            "friendly_name": str(payload.get("friendly_name") or ""),
            "status": str(payload.get("status") or ""),
        }

    def list_recordings(
        self,
        *,
        page_size: int = 50,
        call_sid: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return raw Twilio Recording resources (no MediaAsset creation)."""
        params: dict[str, Any] = {"PageSize": max(1, min(int(page_size), 1000))}
        if call_sid:
            params["CallSid"] = call_sid
        payload = self._request("GET", "Recordings.json", params=params)
        recordings = payload.get("recordings") if isinstance(payload, dict) else None
        if not isinstance(recordings, list):
            return []
        return [r for r in recordings if isinstance(r, dict)]

    def fetch_call(self, call_sid: str) -> dict[str, Any]:
        call_sid = (call_sid or "").strip()
        if not call_sid:
            raise ConnectorConfigurationError("call_sid is required.")
        payload = self._request("GET", f"Calls/{call_sid}.json")
        return payload if isinstance(payload, dict) else {}

    def list_calls_with_recordings(
        self,
        *,
        page_size: int = 50,
    ) -> list[TelephonyCall]:
        """
        Discover recordings and normalize to ``TelephonyCall``.

        Groups by Call SID and keeps the primary (longest) recording per call.
        Optionally enriches with Call resource from/to/start metadata.
        """
        recordings = self.list_recordings(page_size=page_size)
        by_call: dict[str, list[dict[str, Any]]] = {}
        for rec in recordings:
            call_sid = str(rec.get("call_sid") or "").strip()
            if not call_sid:
                continue
            by_call.setdefault(call_sid, []).append(rec)

        calls: list[TelephonyCall] = []
        for call_sid, recs in by_call.items():
            primary = pick_primary_recording(recs)
            if primary is None:
                continue
            call_payload: dict[str, Any] | None = None
            try:
                call_payload = self.fetch_call(call_sid)
            except ConnectorHealthError:
                logger.warning(
                    "Twilio call metadata fetch skipped call_sid=%s", call_sid
                )
            normalized = normalize_twilio_recording(
                primary,
                account_sid=self.account_sid,
                call=call_payload,
                api_base=self.api_base,
            )
            if normalized is not None:
                calls.append(normalized)
        return calls

    def get_recording_for_call(self, call_sid: str) -> TelephonyCall | None:
        """Fetch the primary recording for a single Call SID."""
        call_sid = (call_sid or "").strip()
        if not call_sid:
            return None
        recordings = self.list_recordings(call_sid=call_sid, page_size=50)
        primary = pick_primary_recording(recordings)
        if primary is None:
            return None
        call_payload: dict[str, Any] | None = None
        try:
            call_payload = self.fetch_call(call_sid)
        except ConnectorHealthError:
            call_payload = None
        return normalize_twilio_recording(
            primary,
            account_sid=self.account_sid,
            call=call_payload,
            api_base=self.api_base,
        )
