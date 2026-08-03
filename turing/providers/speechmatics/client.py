from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Literal

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from turing.domain.exceptions import ConfigurationError, ProviderError

logger = logging.getLogger(__name__)

# Controlled client-side retries (urllib3 retries are disabled).
RETRY_BACKOFF_SECONDS: tuple[float, ...] = (5.0, 15.0, 30.0, 60.0, 120.0)
MAX_ATTEMPTS = 5

UNAVAILABLE_MESSAGE = (
    "Speechmatics is temporarily unavailable. Please retry later."
)


@dataclass(frozen=True)
class SpeechmaticsTimeouts:
    """HTTP timeouts for Speechmatics Batch API (seconds)."""

    connect: float = 10.0
    upload: float = 120.0
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


def _disable_urllib3_retries(session: requests.Session) -> None:
    """Prevent urllib3 from silently retrying and blocking workers for minutes."""
    retry = Retry(
        total=0,
        connect=0,
        read=0,
        redirect=0,
        status=0,
        other=0,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)


def _classify_request_exception(exc: BaseException) -> str | None:
    """
    Return a short reason code for retryable transport failures, else None.

    Check SSLError before ConnectionError (SSLError subclasses ConnectionError).
    """
    if isinstance(exc, requests.exceptions.SSLError):
        return "ssl"
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return "connect_timeout"
    if isinstance(exc, requests.exceptions.ReadTimeout):
        return "read_timeout"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "connection"
    return None


def _log_speechmatics(event: str, **fields: Any) -> None:
    parts = [f"[SPEECHMATICS] {event}"]
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(f"{key}={value}")
    message = " ".join(parts)
    if event.endswith("failed"):
        logger.warning(message)
    else:
        logger.info(message)


class SpeechmaticsClient:
    """Thin HTTP client for Speechmatics Batch API v2."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://asr.api.speechmatics.com/v2",
        connect_timeout: float = 10.0,
        upload_timeout: float = 120.0,
        read_timeout: float = 60.0,
        timeout: int | float | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = MAX_ATTEMPTS,
        retry_backoff: tuple[float, ...] = RETRY_BACKOFF_SECONDS,
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
        self._sleep = sleep
        self._max_attempts = max(1, int(max_attempts))
        self._retry_backoff = tuple(float(x) for x in retry_backoff)
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {api_key}"})
        _disable_urllib3_retries(self.session)

    def submit_job(
        self,
        *,
        config: dict[str, Any],
        media_url: str | None = None,
        media_bytes: bytes | None = None,
        filename: str = "audio",
        content_type: str = "application/octet-stream",
        job_id: str | None = None,
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

        def _do() -> requests.Response:
            return self.session.post(
                url,
                data=data,
                files=files,
                timeout=request_timeout,
            )

        return self._request_with_retry(
            operation="submit",
            do_request=_do,
            job_id=job_id,
        )

    def get_job(self, job_id: str, *, log_job_id: str | None = None) -> dict[str, Any]:
        url = f"{self.base_url}/jobs/{job_id}"

        def _do() -> requests.Response:
            return self.session.get(
                url,
                timeout=self.timeouts.as_tuple(kind="read"),
            )

        return self._request_with_retry(
            operation="get_job",
            do_request=_do,
            job_id=log_job_id or job_id,
        )

    def get_transcript(
        self,
        job_id: str,
        *,
        format: str = "json-v2",
        log_job_id: str | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/jobs/{job_id}/transcript"

        def _do() -> requests.Response:
            return self.session.get(
                url,
                params={"format": format},
                timeout=self.timeouts.as_tuple(kind="read"),
            )

        return self._request_with_retry(
            operation="get_transcript",
            do_request=_do,
            job_id=log_job_id or job_id,
        )

    def delete_job(self, job_id: str, *, log_job_id: str | None = None) -> None:
        url = f"{self.base_url}/jobs/{job_id}"

        def _do() -> requests.Response:
            return self.session.delete(
                url,
                timeout=self.timeouts.as_tuple(kind="read"),
            )

        self._request_with_retry(
            operation="delete",
            do_request=_do,
            job_id=log_job_id or job_id,
            allow_empty=True,
        )

    def _request_with_retry(
        self,
        *,
        operation: str,
        do_request: Callable[[], requests.Response],
        job_id: str | None = None,
        allow_empty: bool = False,
    ) -> dict[str, Any]:
        last_reason = "unknown"
        log_job = job_id or "-"

        for attempt in range(1, self._max_attempts + 1):
            started = time.monotonic()
            try:
                response = do_request()
            except requests.RequestException as exc:
                reason = _classify_request_exception(exc)
                elapsed = round(time.monotonic() - started, 1)
                if reason is None:
                    _log_speechmatics(
                        f"{operation} failed",
                        job=log_job,
                        reason="network",
                        attempt=attempt,
                        elapsed=f"{elapsed}s",
                    )
                    raise ProviderError(
                        str(exc),
                        code="PROVIDER_NETWORK",
                        retryable=False,
                        provider_code="speechmatics",
                    ) from exc
                last_reason = reason
                if attempt >= self._max_attempts:
                    _log_speechmatics(
                        f"{operation} failed",
                        job=log_job,
                        reason=reason,
                        attempt=attempt,
                        elapsed=f"{elapsed}s",
                    )
                    raise ProviderError(
                        UNAVAILABLE_MESSAGE,
                        code="PROVIDER_UNAVAILABLE",
                        retryable=False,
                        provider_code="speechmatics",
                    ) from exc
                retry_in = self._backoff_for(attempt)
                _log_speechmatics(
                    f"{operation} failed",
                    job=log_job,
                    reason=reason,
                    attempt=attempt,
                    elapsed=f"{elapsed}s",
                    retry_in=f"{retry_in:g}s",
                )
                self._sleep(retry_in)
                continue

            elapsed = round(time.monotonic() - started, 1)

            if response.status_code >= 500:
                last_reason = "http_5xx"
                if attempt >= self._max_attempts:
                    _log_speechmatics(
                        f"{operation} failed",
                        job=log_job,
                        reason=last_reason,
                        attempt=attempt,
                        elapsed=f"{elapsed}s",
                        status=response.status_code,
                    )
                    raise ProviderError(
                        UNAVAILABLE_MESSAGE,
                        code="PROVIDER_UNAVAILABLE",
                        retryable=False,
                        provider_code="speechmatics",
                    )
                retry_in = self._backoff_for(attempt)
                _log_speechmatics(
                    f"{operation} failed",
                    job=log_job,
                    reason=last_reason,
                    attempt=attempt,
                    elapsed=f"{elapsed}s",
                    status=response.status_code,
                    retry_in=f"{retry_in:g}s",
                )
                self._sleep(retry_in)
                continue

            # Non-retryable HTTP / parse handling (auth, 4xx, etc.)
            if allow_empty and response.status_code in {200, 204, 404}:
                _log_speechmatics(
                    f"{operation} success",
                    job=log_job,
                    elapsed=f"{elapsed}s",
                )
                return {}
            try:
                payload = self._handle(response)
            except ProviderError:
                # Auth / client / quota errors — do not burn retry budget.
                raise

            _log_speechmatics(
                f"{operation} success",
                job=log_job,
                elapsed=f"{elapsed}s",
            )
            return payload

        raise ProviderError(
            UNAVAILABLE_MESSAGE,
            code="PROVIDER_UNAVAILABLE",
            retryable=False,
            provider_code="speechmatics",
        )

    def _backoff_for(self, attempt: int) -> float:
        """Backoff after ``attempt`` (1-based) before the next try."""
        idx = min(attempt - 1, len(self._retry_backoff) - 1)
        return float(self._retry_backoff[idx])

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
            # Retried by _request_with_retry; keep for direct callers.
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
