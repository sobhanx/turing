from __future__ import annotations

import io
import struct
import wave

import pytest

from turing.domain.exceptions import ValidationError
from turing.media.metadata import extract_audio_metadata
from turing.media.validation import validate_audio_upload
from turing.services.media import MediaService
from turing.storage.backends import get_storage_gateway
from turing.storage.media import MediaStorageService


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


@pytest.mark.django_db
def test_valid_wav_upload_persists_metadata_and_storage_key():
    raw = _make_wav_bytes(seconds=0.5, rate=16000, channels=1)
    asset = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(raw),
        filename="hello.wav",
        content_type="audio/wav",
    )
    assert asset.byte_size == len(raw)
    assert asset.audio_format == "wav"
    assert asset.sample_rate_hz == 16000
    assert asset.channels == 1
    assert asset.duration_ms is not None and asset.duration_ms >= 400
    assert asset.object_key
    assert asset.storage_backend
    assert MediaStorageService().exists(asset)
    assert MediaService().read_bytes(asset) == raw


@pytest.mark.django_db
def test_invalid_extension_rejected():
    with pytest.raises(ValidationError, match="Unsupported audio file extension"):
        MediaService().create_from_upload(
            uploaded_file=io.BytesIO(b"not-audio"),
            filename="notes.txt",
            content_type="text/plain",
        )


@pytest.mark.django_db
def test_oversized_upload_rejected():
    from turing.conf import clear_settings_cache
    from turing.models import PlatformConfiguration

    platform = PlatformConfiguration.get_solo()
    platform.max_upload_bytes = 10
    platform.save()
    clear_settings_cache()
    try:
        with pytest.raises(ValidationError, match="max upload size"):
            MediaService().create_from_upload(
                uploaded_file=io.BytesIO(b"0123456789ABCDEF"),
                filename="clip.wav",
                content_type="audio/wav",
            )
    finally:
        platform.max_upload_bytes = 500 * 1024 * 1024
        platform.save()
        clear_settings_cache()


def test_metadata_extraction_success_for_wav():
    raw = _make_wav_bytes(seconds=1.0, rate=8000, channels=2)
    meta = extract_audio_metadata(raw, filename="a.wav", content_type="audio/wav")
    assert meta.audio_format == "wav"
    assert meta.sample_rate_hz == 8000
    assert meta.channels == 2
    assert meta.duration_ms == 1000


def test_metadata_extraction_failure_is_soft():
    meta = extract_audio_metadata(b"not-a-real-wav", filename="broken.wav", content_type="audio/wav")
    assert meta.duration_ms is None
    # Still usable — format hint may remain
    assert meta.audio_format in {"", "wav"}


def test_validate_audio_upload_allows_configured_extensions():
    ext, mime = validate_audio_upload(
        filename="voice.m4a",
        content_type="audio/mp4",
        byte_size=128,
    )
    assert ext == "m4a"
    assert "audio" in mime


@pytest.mark.django_db
def test_storage_gateway_delete_roundtrip():
    gateway = get_storage_gateway()
    key = gateway.save("turing/test/delete-me.bin", b"abc")
    assert gateway.exists(key)
    gateway.delete(key)
    assert not gateway.exists(key)


@pytest.mark.django_db
def test_corrupt_wav_upload_still_saved():
    """Metadata failure must not block a valid-extension upload."""
    asset = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"RIFF....notreally"),
        filename="corrupt.wav",
        content_type="audio/wav",
    )
    assert asset.id
    assert asset.object_key
    assert asset.duration_ms is None
