from __future__ import annotations

"""Speechmatics HTTP client timeout behavior."""

from unittest.mock import MagicMock

import pytest
import requests

from turing.conf import clear_settings_cache, get_turing_settings
from turing.domain.exceptions import ProviderError
from turing.providers.speechmatics.adapter import SpeechmaticsAdapter
from turing.providers.speechmatics.client import SpeechmaticsClient, SpeechmaticsTimeouts


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    clear_settings_cache()
    yield
    clear_settings_cache()


def test_timeouts_as_tuple_upload_vs_read():
    t = SpeechmaticsTimeouts(connect=10.0, upload=900.0, read=45.0)
    assert t.as_tuple(kind="upload") == (10.0, 900.0)
    assert t.as_tuple(kind="read") == (10.0, 45.0)
    assert t.for_post_upload() == 900.0


def test_post_upload_timeout_scalar_avoids_connect_cap():
    """urllib3 sends request bodies under connect_timeout; scalar uses one budget."""
    t = SpeechmaticsTimeouts(connect=30.0, upload=600.0, read=60.0)
    assert t.for_post_upload() == 600.0
    assert t.as_tuple(kind="upload")[0] == 30.0  # would cap writes if used for POST body


def test_client_legacy_timeout_overrides_read_only():
    client = SpeechmaticsClient(api_key="key", timeout=120)
    assert client.timeouts.read == 120.0
    assert client.timeouts.upload == 600.0
    assert client.timeout == 120.0


def test_submit_job_file_upload_uses_upload_timeout(monkeypatch):
    client = SpeechmaticsClient(
        api_key="key",
        connect_timeout=5.0,
        upload_timeout=1200.0,
        read_timeout=30.0,
    )
    response = MagicMock()
    response.status_code = 201
    response.content = b'{"job": {"id": "job-1"}}'
    response.json.return_value = {"job": {"id": "job-1"}}

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
    )
    response = MagicMock()
    response.status_code = 201
    response.content = b'{"job": {"id": "job-1"}}'
    response.json.return_value = {"job": {"id": "job-1"}}

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
    )
    response = MagicMock()
    response.status_code = 201
    response.content = b'{"job": {"id": "job-2"}}'
    response.json.return_value = {"job": {"id": "job-2"}}

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
    )
    response = MagicMock()
    response.status_code = 200
    response.content = b'{"job": {"status": "running"}}'
    response.json.return_value = {"job": {"status": "running"}}

    captured: dict = {}

    def _get(url, timeout=None, params=None):
        captured["timeout"] = timeout
        return response

    client.session.get = _get  # type: ignore[method-assign]
    client.get_job("job-3")
    assert captured["timeout"] == (7.0, 55.0)


def test_get_transcript_uses_upload_timeout():
    client = SpeechmaticsClient(
        api_key="key",
        connect_timeout=6.0,
        upload_timeout=800.0,
        read_timeout=25.0,
    )
    response = MagicMock()
    response.status_code = 200
    response.content = b'{"results": []}'
    response.json.return_value = {"results": []}

    captured: dict = {}

    def _get(url, timeout=None, params=None):
        captured["timeout"] = timeout
        return response

    client.session.get = _get  # type: ignore[method-assign]
    client.get_transcript("job-4")
    assert captured["timeout"] == (6.0, 800.0)


def test_network_timeout_raises_retryable_provider_error():
    client = SpeechmaticsClient(api_key="key")
    client.session.post = MagicMock(  # type: ignore[method-assign]
        side_effect=requests.exceptions.ConnectionError(
            "Connection aborted.",
            TimeoutError("The write operation timed out"),
        )
    )
    with pytest.raises(ProviderError) as exc_info:
        client.submit_job(
            config={"type": "transcription"},
            media_bytes=b"audio",
        )
    assert exc_info.value.code == "PROVIDER_NETWORK"
    assert exc_info.value.retryable is True


@pytest.mark.django_db
def test_adapter_passes_settings_timeouts(settings):
    settings.TURING_SPEECHMATICS_API_KEY = "test-key"
    settings.TURING_SPEECHMATICS_CONNECT_TIMEOUT = 12.5
    settings.TURING_SPEECHMATICS_UPLOAD_TIMEOUT = 1800.0
    clear_settings_cache()

    captured: dict = {}

    class CapturingClient(SpeechmaticsClient):
        def __init__(
            self,
            *,
            api_key: str,
            base_url: str = "",
            connect_timeout: float = 30.0,
            upload_timeout: float = 600.0,
            read_timeout: float = 60.0,
            timeout: int | float | None = None,
        ):
            captured["connect_timeout"] = connect_timeout
            captured["upload_timeout"] = upload_timeout
            super().__init__(
                api_key=api_key,
                base_url=base_url,
                connect_timeout=connect_timeout,
                upload_timeout=upload_timeout,
                read_timeout=read_timeout,
                timeout=timeout,
            )

    import turing.providers.speechmatics.adapter as adapter_mod

    original = adapter_mod.SpeechmaticsClient
    adapter_mod.SpeechmaticsClient = CapturingClient
    try:
        settings_obj = get_turing_settings(refresh=True)
        assert settings_obj.speechmatics_connect_timeout == 12.5
        assert settings_obj.speechmatics_upload_timeout == 1800.0
        SpeechmaticsAdapter()._get_client()
    finally:
        adapter_mod.SpeechmaticsClient = original

    assert captured["connect_timeout"] == 12.5
    assert captured["upload_timeout"] == 1800.0
