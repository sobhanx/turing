"""Phase 2.9 — production storage (S3 / streaming / signed URLs)."""

from __future__ import annotations

import io
import struct
import wave
from unittest.mock import MagicMock

import pytest
from django.core.files.storage import default_storage
from django.test import override_settings

from turing.domain.enums import StorageBackend, UseCase
from turing.domain.exceptions import ValidationError
from turing.media.metadata import extract_audio_metadata_from_path
from turing.providers.types import TranscriptionRequest
from turing.services.job_orchestrator import JobOrchestrator
from turing.services.media import MediaService
from turing.services.transcription import TranscriptionService
from turing.storage.backends import DjangoStorageGateway, StorageGateway, get_storage_gateway
from turing.storage.media import MediaStorageService
from turing.storage.spool import spool_upload


def _make_wav_bytes(*, seconds: float = 0.25, rate: int = 16000, channels: int = 1) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        frame_count = int(rate * seconds)
        silence = struct.pack("<h", 0) * channels
        handle.writeframes(silence * frame_count)
    return buffer.getvalue()


class RecordingGateway(StorageGateway):
    """In-memory gateway that records save content_type and streaming."""

    backend_code = StorageBackend.S3

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.last_content_type = ""
        self.save_was_file_like = False

    def save(self, key, content, *, content_type: str = "") -> str:
        self.last_content_type = content_type
        if isinstance(content, (bytes, bytearray)):
            self.objects[key] = bytes(content)
            self.save_was_file_like = False
        else:
            self.save_was_file_like = True
            if hasattr(content, "seek"):
                try:
                    content.seek(0)
                except Exception:
                    pass
            self.objects[key] = content.read()
        return key

    def open(self, key: str):
        return io.BytesIO(self.objects[key])

    def exists(self, key: str) -> bool:
        return key in self.objects

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def url(self, key: str) -> str:
        return f"https://example.test/{key}"

    def signed_url(self, key: str, *, expires_in: int | None = None) -> str:
        ttl = expires_in or 3600
        return f"https://example.test/{key}?X-Amz-Expires={ttl}&sig=test"


def test_spool_upload_streams_and_hashes():
    raw = b"abcdefgh" * 1000
    with spool_upload(io.BytesIO(raw), max_bytes=10_000_000) as spool:
        assert spool.size == len(raw)
        assert len(spool.checksum) == 64
        with spool.open() as handle:
            assert handle.read() == raw


def test_spool_upload_enforces_max_bytes():
    with pytest.raises(ValidationError, match="max upload size"):
        with spool_upload(io.BytesIO(b"0123456789ABCDEF"), max_bytes=10):
            pass


def test_extract_metadata_from_path(tmp_path):
    raw = _make_wav_bytes(seconds=0.5, rate=8000, channels=1)
    path = tmp_path / "clip.wav"
    path.write_bytes(raw)
    meta = extract_audio_metadata_from_path(str(path), filename="clip.wav")
    assert meta.sample_rate_hz == 8000
    assert meta.duration_ms is not None and meta.duration_ms >= 400


@pytest.mark.django_db
def test_media_storage_streams_file_like_and_sets_content_type():
    gateway = RecordingGateway()
    storage = MediaStorageService(gateway=gateway)
    raw = _make_wav_bytes()
    key = storage.save_upload(
        filename="voice.wav",
        content=io.BytesIO(raw),
        content_type="audio/wav",
    )
    assert gateway.save_was_file_like is True
    assert gateway.last_content_type == "audio/wav"
    assert gateway.objects[key] == raw


@pytest.mark.django_db
def test_media_service_upload_uses_spool_not_only_bytes():
    raw = _make_wav_bytes(seconds=0.3)
    asset = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(raw),
        filename="streamed.wav",
        content_type="audio/wav",
    )
    assert asset.object_key
    assert asset.checksum
    assert asset.byte_size == len(raw)
    assert MediaService().read_bytes(asset) == raw


@pytest.mark.django_db
def test_signed_url_on_media_storage_service():
    gateway = RecordingGateway()
    storage = MediaStorageService(gateway=gateway)
    key = storage.save_upload(filename="a.wav", content=b"abc", content_type="audio/wav")
    from turing.models import MediaAsset

    asset = MediaAsset(object_key=key, storage_backend=StorageBackend.S3)
    url = storage.signed_url(asset, expires_in=120)
    assert "X-Amz-Expires=120" in url
    assert storage.supports_remote_fetch() is True


@pytest.mark.django_db
def test_transcription_prefers_signed_url_for_s3(monkeypatch):
    gateway = RecordingGateway()
    media_service = MediaService(storage=MediaStorageService(gateway=gateway))
    asset = media_service.create_from_upload(
        uploaded_file=io.BytesIO(_make_wav_bytes()),
        filename="remote.wav",
        content_type="audio/wav",
        use_case=UseCase.VOICE_FILE,
    )
    asset.storage_backend = StorageBackend.S3
    asset.save(update_fields=["storage_backend"])

    job = JobOrchestrator().create_transcription_job(
        media=asset, language_code="en", auto_enqueue=False
    )

    # Ensure TranscriptionService uses our S3-capable MediaService path
    monkeypatch.setattr(
        "turing.services.media.MediaService",
        lambda: media_service,
    )
    request = TranscriptionService()._build_request(job)
    assert isinstance(request, TranscriptionRequest)
    assert request.media_url and request.media_url.startswith("https://")
    assert request.media_bytes is None


@pytest.mark.django_db
def test_local_gateway_signed_url_falls_back_to_url():
    gateway = get_storage_gateway(backend_code=StorageBackend.LOCAL)
    assert gateway.supports_remote_fetch() is False
    key = gateway.save("turing/test/signed-local.bin", b"xyz", content_type="application/octet-stream")
    url = gateway.signed_url(key, expires_in=60)
    assert url  # FileSystemStorage URL
    gateway.delete(key)


def test_apply_media_storage_s3_requires_bucket():
    from django.core.exceptions import ImproperlyConfigured

    from config.settings.storage import apply_media_storage

    settings: dict = {"MEDIA_ROOT": "/tmp", "MEDIA_URL": "/media/"}
    with pytest.raises(ImproperlyConfigured, match="TURING_S3_BUCKET"):
        with override_settings():
            import os

            old = os.environ.get("TURING_STORAGE_BACKEND")
            old_bucket = os.environ.get("TURING_S3_BUCKET")
            try:
                os.environ["TURING_STORAGE_BACKEND"] = "s3"
                os.environ.pop("TURING_S3_BUCKET", None)
                os.environ.pop("AWS_STORAGE_BUCKET_NAME", None)
                apply_media_storage(settings)
            finally:
                if old is None:
                    os.environ.pop("TURING_STORAGE_BACKEND", None)
                else:
                    os.environ["TURING_STORAGE_BACKEND"] = old
                if old_bucket is not None:
                    os.environ["TURING_S3_BUCKET"] = old_bucket


def test_apply_media_storage_s3_configures_storages(monkeypatch):
    from config.settings.storage import apply_media_storage

    monkeypatch.setenv("TURING_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("TURING_S3_BUCKET", "turing-media")
    monkeypatch.setenv("TURING_S3_REGION", "eu-west-1")
    monkeypatch.setenv("TURING_SIGNED_URL_TTL_SECONDS", "900")
    settings: dict = {"MEDIA_ROOT": "/tmp", "MEDIA_URL": "/media/"}
    apply_media_storage(settings)
    assert settings["STORAGES"]["default"]["BACKEND"].endswith("S3Boto3Storage")
    opts = settings["STORAGES"]["default"]["OPTIONS"]
    assert opts["bucket_name"] == "turing-media"
    assert opts["querystring_auth"] is True
    assert opts["default_acl"] is None
    assert opts["querystring_expire"] == 900
