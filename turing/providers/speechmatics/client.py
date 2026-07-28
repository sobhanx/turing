from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import requests

from turing.domain.exceptions import ConfigurationError, ProviderError


@dataclass(frozen=True)
class SpeechmaticsTimeouts:
    """HTTP timeouts for Speechmatics Batch API (seconds)."""

    connect: float = 30.0
    upload: float = 600.0
    read: float = 60.0

    def as_tuple(self, *, kind: Literal["upload", "read"] = "read") -> tuple[float, float]:
        """
        Return ``(connect_timeout, read_timeout)`` for ``requests``.

        Suitable for GET/HEAD/DELETE and small POST bodies (e.g. URL-fetch submit).
        """
        read_timeout = self.upload if kind == "upload" else self.read
        return (float(self.connect), float(read_timeout))

    def for_post_upload(self) -> float:
        """
        Timeout for multipart POST uploads.

        urllib3 applies ``connect_timeout`` to the socket while sending the request
        body, so a ``(connect, read)`` tuple caps uploads at ``connect`` even when
        ``read`` is larger. Use a single scalar so connect and body-send share the
        upload budget.
        """
        return float(self.upload)


class SpeechmaticsClient:
    """Thin HTTP client for Speechmatics Batch API v2."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://asr.api.speechmatics.com/v2",
        connect_timeout: float = 30.0,
        upload_timeout: float = 600.0,
        read_timeout: float = 60.0,
        timeout: int | float | None = None,
    ) -> None:
        if not api_key:
            raise ConfigurationError(
                "Speechmatics API key is not configured. "
                "Set it in Admin → Speech provider configs or TURING_SPEECHMATICS_API_KEY."
            )
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        # Backward compatibility: single ``timeout`` overrides read_timeout only.
        if timeout is not None:
            read_timeout = float(timeout)
        self.timeouts = SpeechmaticsTimeouts(
            connect=float(connect_timeout),
            upload=float(upload_timeout),
            read=float(read_timeout),
        )
        # Legacy attribute used by tests / callers introspecting the client.
        self.timeout = self.timeouts.read
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
            config.setdefault("fetch_data", {"url": media_url})
            data = {"config": _json_dumps(config)}
            request_timeout: float | tuple[float, float] = self.timeouts.as_tuple(kind="read")
        elif media_bytes is not None:
            files = {"data_file": (filename, media_bytes, content_type)}
            request_timeout = self.timeouts.for_post_upload()
        else:
            raise ProviderError("Either media_url or media_bytes is required.", retryable=False)

        try:
            response = self.session.post(
                url,
                data=data,
                files=files,
                timeout=request_timeout,
            )
        except requests.RequestException as exc:
            raise ProviderError(str(exc), code="PROVIDER_NETWORK", retryable=True) from exc

        return self._handle(response)

    def get_job(self, job_id: str) -> dict[str, Any]:
        url = f"{self.base_url}/jobs/{job_id}"
        try:
            response = self.session.get(
                url,
                timeout=self.timeouts.as_tuple(kind="read"),
            )
        except requests.RequestException as exc:
            raise ProviderError(str(exc), code="PROVIDER_NETWORK", retryable=True) from exc
        return self._handle(response)

    def get_transcript(self, job_id: str, *, format: str = "json-v2") -> dict[str, Any]:
        url = f"{self.base_url}/jobs/{job_id}/transcript"
        try:
            response = self.session.get(
                url,
                params={"format": format},
                timeout=self.timeouts.as_tuple(kind="upload"),
            )
        except requests.RequestException as exc:
            raise ProviderError(str(exc), code="PROVIDER_NETWORK", retryable=True) from exc
        return self._handle(response)

    def delete_job(self, job_id: str) -> None:
        url = f"{self.base_url}/jobs/{job_id}"
        try:
            response = self.session.delete(
                url,
                timeout=self.timeouts.as_tuple(kind="read"),
            )
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
