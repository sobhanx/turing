from __future__ import annotations

from typing import Any

import requests

from turing.domain.exceptions import ConfigurationError, ProviderError


class SpeechmaticsClient:
    """Thin HTTP client for Speechmatics Batch API v2."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://asr.api.speechmatics.com/v2",
        timeout: int = 60,
    ) -> None:
        if not api_key:
            raise ConfigurationError(
                "Speechmatics API key is not configured. "
                "Set it in Admin → Speech provider configs or TURING_SPEECHMATICS_API_KEY."
            )
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {api_key}"})

    def submit_job(
        self,
        *,
        config: dict[str, Any],
        media_url: str | None = None,
        media_bytes: bytes | None = None,
        filename: str = "audio",
        content_type: str = "application/octet-stream",
    ) -> dict[str, Any]:
        url = f"{self.base_url}/jobs"
        data = {"config": _json_dumps(config)}
        files = None
        if media_url:
            # Speechmatics accepts fetch_data in config; also allow URL-only submit.
            config.setdefault("fetch_data", {"url": media_url})
            data = {"config": _json_dumps(config)}
        elif media_bytes is not None:
            files = {"data_file": (filename, media_bytes, content_type)}
        else:
            raise ProviderError("Either media_url or media_bytes is required.", retryable=False)

        try:
            response = self.session.post(url, data=data, files=files, timeout=self.timeout)
        except requests.RequestException as exc:
            raise ProviderError(str(exc), code="PROVIDER_NETWORK", retryable=True) from exc

        return self._handle(response)

    def get_job(self, job_id: str) -> dict[str, Any]:
        url = f"{self.base_url}/jobs/{job_id}"
        try:
            response = self.session.get(url, timeout=self.timeout)
        except requests.RequestException as exc:
            raise ProviderError(str(exc), code="PROVIDER_NETWORK", retryable=True) from exc
        return self._handle(response)

    def get_transcript(self, job_id: str, *, format: str = "json-v2") -> dict[str, Any]:
        url = f"{self.base_url}/jobs/{job_id}/transcript"
        try:
            response = self.session.get(
                url,
                params={"format": format},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise ProviderError(str(exc), code="PROVIDER_NETWORK", retryable=True) from exc
        return self._handle(response)

    def delete_job(self, job_id: str) -> None:
        url = f"{self.base_url}/jobs/{job_id}"
        try:
            response = self.session.delete(url, timeout=self.timeout)
        except requests.RequestException as exc:
            raise ProviderError(str(exc), code="PROVIDER_NETWORK", retryable=True) from exc
        if response.status_code not in {200, 204, 404}:
            self._handle(response)

    def _handle(self, response: requests.Response) -> dict[str, Any]:
        if response.status_code in {401, 403}:
            raise ProviderError(
                "Speechmatics authentication failed.",
                code="PROVIDER_AUTH",
                retryable=False,
                provider_code="speechmatics",
            )
        if response.status_code == 429:
            raise ProviderError(
                "Speechmatics rate limit exceeded.",
                code="PROVIDER_QUOTA",
                retryable=True,
                provider_code="speechmatics",
            )
        if response.status_code >= 500:
            raise ProviderError(
                f"Speechmatics server error: {response.status_code}",
                code="PROVIDER_SERVER",
                retryable=True,
                provider_code="speechmatics",
            )
        if response.status_code >= 400:
            detail = _safe_text(response)
            raise ProviderError(
                f"Speechmatics client error: {detail}",
                code="PROVIDER_CLIENT",
                retryable=False,
                provider_code="speechmatics",
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(
                "Invalid JSON from Speechmatics.",
                code="PROVIDER_RESPONSE",
                retryable=True,
            ) from exc


def _json_dumps(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload)


def _safe_text(response: requests.Response) -> str:
    try:
        data = response.json()
        return str(data)
    except Exception:
        return response.text[:500]
