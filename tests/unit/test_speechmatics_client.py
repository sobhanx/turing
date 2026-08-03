from __future__ import annotations

"""Speechmatics HTTP client timeout and retry behavior."""

from unittest.mock import MagicMock

import pytest
import requests

from turing.conf import clear_settings_cache, get_turing_settings
from turing.domain.exceptions import ProviderError
from turing.providers.speechmatics.adapter import SpeechmaticsAdapter
from turing.providers.speechmatics.client import (
    MAX_ATTEMPTS,
    RETRY_BACKOFF_SECONDS,
    UNAVAILABLE_MESSAGE,
    SpeechmaticsClient,
    SpeechmaticsTimeouts,
)


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    clear_settings_cache()
    yield
    clear_settings_cache()


def _ok_response(payload: dict | None = None, status: int = 201) -> MagicMock:
    response = MagicMock()
    response.status_code = status
    body = payload if payload is not None else {"job": {"id": "job-1"}}
    import json

    raw = json.dumps(body).encode()
    response.content = raw
    response.json.return_value = body
    return response


def test_timeouts_as_tuple_upload_vs_read():
    t = SpeechmaticsTimeouts(connect=10.0, upload=900.0, read=45.0)
    assert t.as_tuple(kind="upload") == (10.0, 900.0)
    assert t.as_tuple(kind="read") == (10.0, 45.0)
    assert t.for_post_upload() == 900.0


def test_default_timeouts_are_bounded():
    t = SpeechmaticsTimeouts()
    assert t.connect == 10.0
    assert t.read == 60.0
    assert t.upload == 120.0


def test_post_upload_timeout_scalar_avoids_connect_cap():
    """urllib3 sends request bodies under connect_timeout; scalar uses one budget."""
    t = SpeechmaticsTimeouts(connect=30.0, upload=600.0, read=60.0)
    assert t.for_post_upload() == 600.0
    assert t.as_tuple(kind="upload")[0] == 30.0  # would cap writes if used for POST body


def test_client_legacy_timeout_overrides_read_only():
    client = SpeechmaticsClient(api_key="key", timeout=120)
    assert client.timeouts.read == 120.0
    assert client.timeouts.upload == 120.0
    assert client.timeout == 120.0


def test_submit_job_file_upload_uses_upload_timeout(monkeypatch):
    client = SpeechmaticsClient(
        api_key="key",
        connect_timeout=5.0,
        upload_timeout=1200.0,
        read_timeout=30.0,
        sleep=lambda _s: None,
    )
    response = _ok_response()
    captured: dict = {}

    def _post(url, data=None, files=None, timeout=None):
        captured["timeout"] = timeout
        captured["files"] = files
        return response

    client.session.post = _post  # type: ignore[method-assign]
    client.submit_job(
        config={"type": "transcription"},
        media_bytes=b"x" * 1024,
        filename="big.wav",
    )
    assert captured["timeout"] == 1200.0
    assert captured["files"] is not None


def test_submit_job_bytes_passes_scalar_not_connect_read_tuple():
    """Regression: (30, 600) tuple caps body send at connect=30s in urllib3."""
    client = SpeechmaticsClient(
        api_key="key",
        connect_timeout=30.0,
        upload_timeout=600.0,
        sleep=lambda _s: None,
    )
    response = _ok_response()
    captured: dict = {}

    def _post(url, data=None, files=None, timeout=None):
        captured["timeout"] = timeout
        return response

    client.session.post = _post  # type: ignore[method-assign]
    client.submit_job(
        config={"type": "transcription"},
        media_bytes=b"x" * 1024,
        filename="big.wav",
    )
    assert captured["timeout"] == 600.0
    assert captured["timeout"] != (30.0, 600.0)


def test_submit_job_url_fetch_uses_read_timeout(monkeypatch):
    client = SpeechmaticsClient(
        api_key="key",
        connect_timeout=8.0,
        upload_timeout=900.0,
        read_timeout=40.0,
        sleep=lambda _s: None,
    )
    response = _ok_response({"job": {"id": "job-2"}})
    captured: dict = {}

    def _post(url, data=None, files=None, timeout=None):
        captured["timeout"] = timeout
        captured["files"] = files
        return response

    client.session.post = _post  # type: ignore[method-assign]
    client.submit_job(
        config={"type": "transcription"},
        media_url="https://example.com/audio.wav",
    )
    assert captured["timeout"] == (8.0, 40.0)
    assert captured["files"] is None


def test_get_job_uses_read_timeout():
    client = SpeechmaticsClient(
        api_key="key",
        connect_timeout=7.0,
        upload_timeout=500.0,
        read_timeout=55.0,
        sleep=lambda _s: None,
    )
    response = _ok_response({"job": {"status": "running"}}, status=200)
    captured: dict = {}

    def _get(url, timeout=None, params=None):
        captured["timeout"] = timeout
        return response

    client.session.get = _get  # type: ignore[method-assign]
    client.get_job("job-3")
    assert captured["timeout"] == (7.0, 55.0)


def test_get_transcript_uses_read_timeout():
    client = SpeechmaticsClient(
        api_key="key",
        connect_timeout=6.0,
        upload_timeout=800.0,
        read_timeout=25.0,
        sleep=lambda _s: None,
    )
    response = _ok_response({"results": []}, status=200)
    captured: dict = {}

    def _get(url, timeout=None, params=None):
        captured["timeout"] = timeout
        return response

    client.session.get = _get  # type: ignore[method-assign]
    client.get_transcript("job-4")
    assert captured["timeout"] == (6.0, 25.0)


def test_urllib3_retries_disabled():
    client = SpeechmaticsClient(api_key="key", sleep=lambda _s: None)
    adapter = client.session.get_adapter("https://asr.api.speechmatics.com")
    assert adapter.max_retries.total == 0


@pytest.mark.parametrize(
    "exc_factory,reason",
    [
        (lambda: requests.exceptions.SSLError("ssl boom"), "ssl"),
        (lambda: requests.exceptions.ConnectTimeout("connect boom"), "connect_timeout"),
        (lambda: requests.exceptions.ReadTimeout("read boom"), "read_timeout"),
        (lambda: requests.exceptions.ConnectionError("conn boom"), "connection"),
    ],
)
def test_retryable_network_errors_exhaust_to_unavailable(exc_factory, reason, caplog):
    sleeps: list[float] = []
    client = SpeechmaticsClient(
        api_key="key",
        sleep=sleeps.append,
        max_attempts=MAX_ATTEMPTS,
        retry_backoff=RETRY_BACKOFF_SECONDS,
    )
    client.session.post = MagicMock(side_effect=exc_factory())  # type: ignore[method-assign]

    with caplog.at_level("WARNING"), pytest.raises(ProviderError) as exc_info:
        client.submit_job(
            config={"type": "transcription"},
            media_bytes=b"audio",
            job_id="pj-1",
        )

    assert exc_info.value.code == "PROVIDER_UNAVAILABLE"
    assert exc_info.value.retryable is False
    assert UNAVAILABLE_MESSAGE in exc_info.value.message
    assert client.session.post.call_count == MAX_ATTEMPTS
    assert sleeps == list(RETRY_BACKOFF_SECONDS[: MAX_ATTEMPTS - 1])
    assert any("[SPEECHMATICS]" in r.message and reason in r.message for r in caplog.records)


def test_http_500_retries_then_unavailable():
    sleeps: list[float] = []
    client = SpeechmaticsClient(api_key="key", sleep=sleeps.append)
    response = MagicMock()
    response.status_code = 500
    response.content = b'{"error": "boom"}'
    response.json.return_value = {"error": "boom"}
    response.text = '{"error": "boom"}'
    client.session.post = MagicMock(return_value=response)  # type: ignore[method-assign]

    with pytest.raises(ProviderError) as exc_info:
        client.submit_job(config={"type": "transcription"}, media_bytes=b"audio")

    assert exc_info.value.code == "PROVIDER_UNAVAILABLE"
    assert client.session.post.call_count == MAX_ATTEMPTS
    assert sleeps == list(RETRY_BACKOFF_SECONDS[: MAX_ATTEMPTS - 1])


def test_success_after_retry():
    sleeps: list[float] = []
    client = SpeechmaticsClient(api_key="key", sleep=sleeps.append)
    ok = _ok_response({"job": {"id": "recovered"}})
    client.session.post = MagicMock(  # type: ignore[method-assign]
        side_effect=[
            requests.exceptions.SSLError("ssl boom"),
            ok,
        ]
    )

    result = client.submit_job(
        config={"type": "transcription"},
        media_bytes=b"audio",
        job_id="pj-ok",
    )
    assert result["job"]["id"] == "recovered"
    assert client.session.post.call_count == 2
    assert sleeps == [5.0]


def test_auth_error_not_retried():
    sleeps: list[float] = []
    client = SpeechmaticsClient(api_key="key", sleep=sleeps.append)
    response = MagicMock()
    response.status_code = 401
    response.content = b"{}"
    response.json.return_value = {}
    response.text = "{}"
    client.session.post = MagicMock(return_value=response)  # type: ignore[method-assign]

    with pytest.raises(ProviderError) as exc_info:
        client.submit_job(config={"type": "transcription"}, media_bytes=b"audio")

    assert exc_info.value.code == "PROVIDER_AUTH"
    assert client.session.post.call_count == 1
    assert sleeps == []


@pytest.mark.django_db
def test_exhausted_retries_mark_job_failed():
    """Submit path marks ProcessingJob FAILED with unavailable message."""
    import io

    from django.contrib.auth import get_user_model

    from turing.domain.enums import JobStatus, UseCase
    from turing.models import Organization
    from turing.providers.base import STTProvider
    from turing.providers.registry import ProviderRegistry
    from turing.providers.types import TranscriptionRequest
    from turing.services.job_orchestrator import JobOrchestrator
    from turing.services.media import MediaService
    from turing.services.transcription import TranscriptionService

    User = get_user_model()
    user = User.objects.create_superuser("sm-retry", "sm@example.com", "pass")
    org = Organization.get_default()
    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="retry.wav",
        use_case=UseCase.GENERIC,
        organization=org,
        uploaded_by=user,
    )
    job = JobOrchestrator().create_transcription_job(
        media=media,
        created_by=user,
        language_code="fa",
        auto_enqueue=False,
    )

    class FailingProvider(STTProvider):
        code = "speechmatics"
        display_name = "Speechmatics"

        def submit(self, request: TranscriptionRequest):
            raise ProviderError(
                UNAVAILABLE_MESSAGE,
                code="PROVIDER_UNAVAILABLE",
                retryable=False,
                provider_code="speechmatics",
            )

        def get_status(self, handle):
            raise NotImplementedError

        def fetch_result(self, handle):
            raise NotImplementedError

    original_cls = type(ProviderRegistry.get("speechmatics"))
    ProviderRegistry.register(FailingProvider)
    try:
        with pytest.raises(ProviderError) as exc_info:
            TranscriptionService().submit(str(job.id))
        assert exc_info.value.code == "PROVIDER_UNAVAILABLE"
    finally:
        ProviderRegistry.register(original_cls)

    job.refresh_from_db()
    assert job.status == JobStatus.FAILED
    assert job.error_code == "PROVIDER_UNAVAILABLE"
    assert UNAVAILABLE_MESSAGE in job.error_message


@pytest.mark.django_db
def test_adapter_passes_settings_timeouts(settings):
    settings.TURING_SPEECHMATICS_API_KEY = "test-key"
    settings.TURING_SPEECHMATICS_CONNECT_TIMEOUT = 12.5
    settings.TURING_SPEECHMATICS_UPLOAD_TIMEOUT = 1800.0
    settings.TURING_SPEECHMATICS_READ_TIMEOUT = 33.0
    clear_settings_cache()

    captured: dict = {}

    class CapturingClient(SpeechmaticsClient):
        def __init__(
            self,
            *,
            api_key: str,
            base_url: str = "",
            connect_timeout: float = 10.0,
            upload_timeout: float = 120.0,
            read_timeout: float = 60.0,
            timeout: int | float | None = None,
            **kwargs,
        ):
            captured["connect_timeout"] = connect_timeout
            captured["upload_timeout"] = upload_timeout
            captured["read_timeout"] = read_timeout
            super().__init__(
                api_key=api_key,
                base_url=base_url,
                connect_timeout=connect_timeout,
                upload_timeout=upload_timeout,
                read_timeout=read_timeout,
                timeout=timeout,
                **kwargs,
            )

    import turing.providers.speechmatics.adapter as adapter_mod

    original = adapter_mod.SpeechmaticsClient
    adapter_mod.SpeechmaticsClient = CapturingClient
    try:
        settings_obj = get_turing_settings(refresh=True)
        assert settings_obj.speechmatics_connect_timeout == 12.5
        assert settings_obj.speechmatics_upload_timeout == 1800.0
        assert settings_obj.speechmatics_read_timeout == 33.0
        SpeechmaticsAdapter()._get_client()
    finally:
        adapter_mod.SpeechmaticsClient = original

    assert captured["connect_timeout"] == 12.5
    assert captured["upload_timeout"] == 1800.0
    assert captured["read_timeout"] == 33.0
