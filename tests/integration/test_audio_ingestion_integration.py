from __future__ import annotations

import io
import struct
import subprocess
import tempfile
import wave
from pathlib import Path

import pytest

from turing.domain.enums import ArtifactStatus, IngestStatus
from turing.domain.ingestion import (
    CANONICAL_CHANNELS,
    CANONICAL_CODEC,
    CANONICAL_SAMPLE_RATE_HZ,
    is_stt_compatible,
)
from turing.media.inspection import resolve_ffprobe_path
from turing.media.normalization import resolve_ffmpeg_path
from turing.services.job_orchestrator import JobOrchestrator
from turing.services.media import MediaService
from turing.services.media_ingestion import MediaIngestionService

pytestmark = pytest.mark.integration


def _canonical_wav_bytes(duration_ms: int = 500) -> bytes:
    frames = int(CANONICAL_SAMPLE_RATE_HZ * (duration_ms / 1000.0))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(CANONICAL_CHANNELS)
        wav.setsampwidth(2)
        wav.setframerate(CANONICAL_SAMPLE_RATE_HZ)
        wav.writeframes(struct.pack("<" + "h" * frames, *([0] * frames)))
    return buffer.getvalue()


def _stereo_wav_bytes(duration_ms: int = 500) -> bytes:
    sample_rate_hz = 44_100
    frames = int(sample_rate_hz * (duration_ms / 1000.0))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate_hz)
        wav.writeframes(struct.pack("<" + "h" * (frames * 2), *([0] * (frames * 2))))
    return buffer.getvalue()


def _ffmpeg_transcode(input_bytes: bytes, *, input_suffix: str, output_suffix: str) -> bytes:
    ffmpeg = resolve_ffmpeg_path()
    if not ffmpeg:
        pytest.skip("ffmpeg not available")

    with tempfile.TemporaryDirectory() as tmp:
        input_path = Path(tmp) / f"input{input_suffix}"
        output_path = Path(tmp) / f"output{output_suffix}"
        input_path.write_bytes(input_bytes)

        if output_suffix == ".mp3":
            codec_args = ["-c:a", "libmp3lame", "-b:a", "128k"]
        elif output_suffix == ".m4a":
            codec_args = ["-c:a", "aac", "-b:a", "128k"]
        elif output_suffix == ".webm":
            codec_args = ["-c:a", "libopus", "-b:a", "64k"]
        elif output_suffix == ".ogg":
            codec_args = ["-c:a", "libopus", "-b:a", "64k"]
        else:
            codec_args = []

        completed = subprocess.run(
            [ffmpeg, "-y", "-i", str(input_path), *codec_args, str(output_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            pytest.fail(f"ffmpeg transcode failed: {completed.stderr}")

        return output_path.read_bytes()


@pytest.fixture
def media_service():
    return MediaService()


@pytest.fixture
def job_factory(media_service):
    from turing.domain.enums import UseCase

    def _create(content: bytes, filename: str):
        media = media_service.create_from_upload(
            uploaded_file=io.BytesIO(content),
            filename=filename,
            use_case=UseCase.MEETING,
        )
        return JobOrchestrator().create_transcription_job(
            media=media,
            language_code="en",
            auto_enqueue=False,
        )

    return _create


@pytest.mark.django_db
def test_wav_16khz_mono_pcm_passes(job_factory):
    if not resolve_ffprobe_path():
        pytest.skip("ffprobe not available")

    job = job_factory(_canonical_wav_bytes(), "canonical.wav")
    result = MediaIngestionService().prepare_for_job(job)
    job.refresh_from_db()

    assert result.status == IngestStatus.SUCCEEDED
    assert result.used_original is True
    assert result.artifact.status == ArtifactStatus.SKIPPED
    assert job.ingest_status == IngestStatus.SUCCEEDED
    assert job.ingest_artifact_id is None


@pytest.mark.django_db
def test_wav_stereo_441_normalizes(job_factory):
    if not resolve_ffprobe_path() or not resolve_ffmpeg_path():
        pytest.skip("ffprobe/ffmpeg not available")

    job = job_factory(_stereo_wav_bytes(), "stereo.wav")
    result = MediaIngestionService().prepare_for_job(job)
    job.refresh_from_db()

    assert result.status == IngestStatus.SUCCEEDED
    assert result.used_original is False
    assert result.artifact.status == ArtifactStatus.READY
    assert job.ingest_artifact_id == result.artifact.id
    assert result.artifact.audio_codec == CANONICAL_CODEC
    assert result.artifact.sample_rate_hz == CANONICAL_SAMPLE_RATE_HZ
    assert result.artifact.channels == CANONICAL_CHANNELS


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("filename", "input_suffix", "output_suffix"),
    [
        ("sample.mp3", ".wav", ".mp3"),
        ("sample.m4a", ".wav", ".m4a"),
        ("sample.webm", ".wav", ".webm"),
        ("sample.ogg", ".wav", ".ogg"),
    ],
)
def test_encoded_formats_normalize(job_factory, filename, input_suffix, output_suffix):
    if not resolve_ffprobe_path() or not resolve_ffmpeg_path():
        pytest.skip("ffprobe/ffmpeg not available")

    encoded = _ffmpeg_transcode(
        _stereo_wav_bytes(),
        input_suffix=input_suffix,
        output_suffix=output_suffix,
    )
    job = job_factory(encoded, filename)
    result = MediaIngestionService().prepare_for_job(job)
    job.refresh_from_db()

    assert result.status == IngestStatus.SUCCEEDED
    assert result.artifact.status == ArtifactStatus.READY
    assert job.ingest_artifact_id == result.artifact.id
    assert result.artifact.audio_codec == CANONICAL_CODEC


@pytest.mark.django_db
def test_corrupt_bytes_rejected(job_factory):
    if not resolve_ffprobe_path():
        pytest.skip("ffprobe not available")

    job = job_factory(b"this-is-not-audio", "broken.wav")

    with pytest.raises(Exception) as exc_info:
        MediaIngestionService().prepare_for_job(job)

    from turing.domain.exceptions import IngestionError

    assert isinstance(exc_info.value, IngestionError)
    job.refresh_from_db()
    assert job.ingest_status == IngestStatus.PENDING


@pytest.mark.django_db
def test_fake_extension_detected_and_normalized(job_factory):
    if not resolve_ffprobe_path() or not resolve_ffmpeg_path():
        pytest.skip("ffprobe/ffmpeg not available")

    stereo_wav = _stereo_wav_bytes()
    job = job_factory(stereo_wav, "mislabeled.mp3")
    result = MediaIngestionService().prepare_for_job(job)

    assert result.status == IngestStatus.SUCCEEDED
    assert result.artifact.status == ArtifactStatus.READY
    assert result.probe is not None
    assert result.probe.format in {"wav", "wave"}
    assert not is_stt_compatible(result.probe)
