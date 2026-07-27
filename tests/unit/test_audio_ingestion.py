from __future__ import annotations

import io
import os
import struct
import subprocess
import wave
from unittest.mock import MagicMock

import pytest

from turing.domain.enums import ArtifactStatus, IngestStatus, JobStatus
from turing.domain.exceptions import IngestionError
from turing.domain.ingestion import (
    CANONICAL_CHANNELS,
    CANONICAL_CODEC,
    CANONICAL_SAMPLE_RATE_HZ,
    AudioProbeResult,
    is_stt_compatible,
    needs_normalization,
)
from turing.domain.pipeline import compute_poll_timeout_seconds
from turing.media.inspection import AudioInspectionService, resolve_ffprobe_path
from turing.models import MediaProcessingArtifact
from turing.services.media_ingestion import MediaIngestionService, _MediaPathContext
from turing.services.transcription import TranscriptionService


def _canonical_wav_bytes(duration_ms: int = 500) -> bytes:
    frames = int(CANONICAL_SAMPLE_RATE_HZ * (duration_ms / 1000.0))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(CANONICAL_CHANNELS)
        wav.setsampwidth(2)
        wav.setframerate(CANONICAL_SAMPLE_RATE_HZ)
        wav.writeframes(struct.pack("<" + "h" * frames, *([0] * frames)))
    return buffer.getvalue()


def _stereo_wav_bytes(
    *,
    sample_rate_hz: int = 44_100,
    duration_ms: int = 500,
) -> bytes:
    frames = int(sample_rate_hz * (duration_ms / 1000.0))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate_hz)
        wav.writeframes(struct.pack("<" + "h" * (frames * 2), *([0] * (frames * 2))))
    return buffer.getvalue()


def _incompatible_probe() -> AudioProbeResult:
    return AudioProbeResult(
        format="mov",
        codec="aac",
        duration_ms=1000,
        sample_rate_hz=44100,
        channels=2,
        bitrate=128000,
        readable=True,
    )


def _compatible_probe() -> AudioProbeResult:
    return AudioProbeResult(
        format="wav",
        codec=CANONICAL_CODEC,
        duration_ms=1000,
        sample_rate_hz=CANONICAL_SAMPLE_RATE_HZ,
        channels=CANONICAL_CHANNELS,
        bitrate=256000,
        readable=True,
    )


def test_compatible_wav_skips_normalization():
    probe = _compatible_probe()
    assert is_stt_compatible(probe)
    assert not needs_normalization(probe)


def test_incompatible_m4a_needs_normalization():
    probe = _incompatible_probe()
    assert not is_stt_compatible(probe)
    assert needs_normalization(probe)


def test_fake_mp3_extension_with_wav_codec_detected():
    probe = AudioProbeResult(
        format="wav",
        codec="pcm_s16le",
        duration_ms=500,
        sample_rate_hz=44100,
        channels=1,
        bitrate=128000,
        readable=True,
    )
    assert not is_stt_compatible(probe)


def test_compute_poll_timeout_scales_with_duration():
    assert compute_poll_timeout_seconds(
        base_timeout_seconds=1800,
        expected_duration_ms=3 * 60 * 60 * 1000,
        multiplier=2.0,
    ) == 21600


@pytest.mark.django_db
def test_ingestion_fails_when_ffprobe_missing(monkeypatch, db):
    from turing.domain.enums import UseCase
    from turing.services.job_orchestrator import JobOrchestrator
    from turing.services.media import MediaService

    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(_canonical_wav_bytes()),
        filename="meeting.wav",
        use_case=UseCase.MEETING,
    )
    job = JobOrchestrator().create_transcription_job(
        media=media,
        language_code="en",
        auto_enqueue=False,
    )

    inspector = MagicMock()
    inspector.probe.return_value = AudioProbeResult(
        format="",
        codec="",
        duration_ms=None,
        sample_rate_hz=None,
        channels=None,
        bitrate=None,
        readable=False,
        error_message="ffprobe_not_available",
    )
    service = MediaIngestionService(inspector=inspector)
    with pytest.raises(IngestionError) as exc_info:
        service.prepare_for_job(job)
    assert exc_info.value.code == "INGEST_PROBE_FAILED"


@pytest.mark.django_db
def test_compatible_wav_creates_skipped_artifact(monkeypatch, db):
    from turing.domain.enums import UseCase
    from turing.services.job_orchestrator import JobOrchestrator
    from turing.services.media import MediaService

    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(_canonical_wav_bytes()),
        filename="meeting.wav",
        use_case=UseCase.MEETING,
    )
    original_checksum = media.checksum
    job = JobOrchestrator().create_transcription_job(
        media=media,
        language_code="en",
        auto_enqueue=False,
    )

    inspector = MagicMock()
    inspector.probe.return_value = _compatible_probe()
    service = MediaIngestionService(inspector=inspector, normalizer=MagicMock())
    result = service.prepare_for_job(job)

    media.refresh_from_db()
    job.refresh_from_db()
    assert media.checksum == original_checksum
    assert result.used_original is True
    assert result.status == IngestStatus.SUCCEEDED
    assert result.artifact is not None
    assert result.artifact.status == ArtifactStatus.SKIPPED
    assert job.ingest_status == IngestStatus.SUCCEEDED


@pytest.mark.django_db
def test_incompatible_audio_normalizes_and_creates_artifact(monkeypatch, db):
    from turing.domain.enums import UseCase
    from turing.media.normalization import NormalizationResult
    from turing.services.job_orchestrator import JobOrchestrator
    from turing.services.media import MediaService

    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"not-really-mp3"),
        filename="call.mp3",
        use_case=UseCase.CRM_CALL,
    )
    original_checksum = media.checksum
    job = JobOrchestrator().create_transcription_job(
        media=media,
        language_code="en",
        auto_enqueue=False,
    )

    inspector = MagicMock()
    inspector.probe.side_effect = [
        _incompatible_probe(),
        _compatible_probe(),
    ]

    normalizer = MagicMock()
    normalizer.normalize.return_value = NormalizationResult(
        output_path="/tmp/out.wav",
        probe=_compatible_probe(),
        success=True,
    )

    def fake_local_path(self, media):
        class Ctx:
            def __enter__(self_inner):
                return "/tmp/in.bin"

            def __exit__(self_inner, *args):
                return None

        return Ctx()

    monkeypatch.setattr(MediaIngestionService, "_local_media_path", fake_local_path)
    monkeypatch.setattr(
        MediaIngestionService,
        "_persist_normalized_artifact",
        lambda self, media, normalized_path, source_probe: MediaProcessingArtifact.objects.create(
            media=media,
            organization=media.organization,
            kind="normalized",
            status=ArtifactStatus.READY,
            storage_backend=media.storage_backend,
            object_key="artifacts/normalized.wav",
            byte_size=100,
            checksum="norm",
            content_type="audio/wav",
            audio_format="wav",
            audio_codec=CANONICAL_CODEC,
        ),
    )
    monkeypatch.setattr("turing.services.media_ingestion.os.path.exists", lambda path: True)
    monkeypatch.setattr("turing.services.media_ingestion.os.unlink", lambda path: None)

    service = MediaIngestionService(inspector=inspector, normalizer=normalizer)
    result = service.prepare_for_job(job)

    media.refresh_from_db()
    job.refresh_from_db()
    assert media.checksum == original_checksum
    assert result.artifact is not None
    assert result.artifact.status == ArtifactStatus.READY
    assert result.status == IngestStatus.SUCCEEDED
    assert job.ingest_artifact_id == result.artifact.id
    assert job.ingest_status == IngestStatus.SUCCEEDED


@pytest.mark.django_db
def test_ffmpeg_failure_raises_ingestion_error(monkeypatch, db):
    from turing.domain.enums import UseCase
    from turing.media.normalization import NormalizationResult
    from turing.services.job_orchestrator import JobOrchestrator
    from turing.services.media import MediaService

    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="call.m4a",
        use_case=UseCase.CRM_CALL,
    )
    job = JobOrchestrator().create_transcription_job(
        media=media,
        language_code="en",
        auto_enqueue=False,
    )

    inspector = MagicMock()
    inspector.probe.return_value = _incompatible_probe()
    normalizer = MagicMock()
    normalizer.normalize.return_value = NormalizationResult(
        output_path="/tmp/out.wav",
        probe=AudioProbeResult(
            format="",
            codec="",
            duration_ms=None,
            sample_rate_hz=None,
            channels=None,
            bitrate=None,
            readable=False,
            error_message="ffmpeg_failed",
        ),
        success=False,
        error_message="ffmpeg_failed",
    )

    def fake_local_path(self, media):
        class Ctx:
            def __enter__(self_inner):
                return "/tmp/in.bin"

            def __exit__(self_inner, *args):
                return None

        return Ctx()

    monkeypatch.setattr(MediaIngestionService, "_local_media_path", fake_local_path)
    service = MediaIngestionService(inspector=inspector, normalizer=normalizer)
    with pytest.raises(IngestionError) as exc_info:
        service.prepare_for_job(job)
    assert exc_info.value.code == "INGEST_NORMALIZE_FAILED"
    assert MediaProcessingArtifact.objects.filter(media=media, status="ready").count() == 0


@pytest.mark.django_db
def test_unreadable_audio_raises_ingestion_error(monkeypatch, db):
    from turing.domain.enums import UseCase
    from turing.services.job_orchestrator import JobOrchestrator
    from turing.services.media import MediaService

    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"corrupt"),
        filename="call.wav",
        use_case=UseCase.CRM_CALL,
    )
    job = JobOrchestrator().create_transcription_job(
        media=media,
        language_code="en",
        auto_enqueue=False,
    )

    inspector = MagicMock()
    inspector.probe.return_value = AudioProbeResult(
        format="",
        codec="",
        duration_ms=None,
        sample_rate_hz=None,
        channels=None,
        bitrate=None,
        readable=False,
        error_message="no_audio_stream",
    )
    service = MediaIngestionService(inspector=inspector)
    with pytest.raises(IngestionError) as exc_info:
        service.prepare_for_job(job)
    assert exc_info.value.code == "INGEST_UNREADABLE"


@pytest.mark.django_db
def test_local_media_path_exists_during_context(db):
    from turing.domain.enums import UseCase
    from turing.services.media import MediaService

    if not resolve_ffprobe_path():
        pytest.skip("ffprobe not available")

    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(_canonical_wav_bytes()),
        filename="meeting.wav",
        use_case=UseCase.MEETING,
    )
    service = MediaService()
    inspector = AudioInspectionService()

    with _MediaPathContext(service, media) as local_path:
        assert os.path.exists(local_path)
        probe = inspector.probe(local_path)
        assert probe.readable is True
        assert is_stt_compatible(probe)


@pytest.mark.django_db
def test_real_ffprobe_on_canonical_wav(db):
    from turing.domain.enums import UseCase
    from turing.services.job_orchestrator import JobOrchestrator
    from turing.services.media import MediaService

    if not resolve_ffprobe_path():
        pytest.skip("ffprobe not available")

    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(_canonical_wav_bytes()),
        filename="meeting.wav",
        use_case=UseCase.MEETING,
    )
    job = JobOrchestrator().create_transcription_job(
        media=media,
        language_code="en",
        auto_enqueue=False,
    )

    result = MediaIngestionService().prepare_for_job(job)
    job.refresh_from_db()

    assert result.status == IngestStatus.SUCCEEDED
    assert result.used_original is True
    assert result.artifact.status == ArtifactStatus.SKIPPED
    assert job.ingest_status == IngestStatus.SUCCEEDED


@pytest.mark.django_db
def test_corrupt_bytes_fail_before_stt(monkeypatch, db):
    from turing.domain.enums import UseCase
    from turing.services.job_orchestrator import JobOrchestrator
    from turing.services.media import MediaService
    from turing.tasks import ingestion as ingestion_tasks

    if not resolve_ffprobe_path():
        pytest.skip("ffprobe not available")

    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"not-audio-at-all"),
        filename="broken.wav",
        use_case=UseCase.CRM_CALL,
    )
    job = JobOrchestrator().create_transcription_job(
        media=media,
        language_code="en",
        auto_enqueue=False,
    )

    scheduled: list[str] = []
    monkeypatch.setattr(
        "turing.tasks.transcription.submit_transcription_job.delay",
        lambda job_id: scheduled.append(job_id),
    )

    result = ingestion_tasks.prepare_media_for_transcription.run(str(job.id))
    job.refresh_from_db()

    assert result.startswith("failed:")
    assert scheduled == []
    assert job.status == JobStatus.FAILED
    assert job.ingest_status == IngestStatus.FAILED
    assert job.ingest_error
    assert job.error_code


@pytest.mark.django_db
def test_build_request_uses_artifact_bytes(monkeypatch, db):
    from turing.domain.enums import UseCase
    from turing.services.job_orchestrator import JobOrchestrator
    from turing.services.media import MediaService

    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(_canonical_wav_bytes()),
        filename="meeting.wav",
        use_case=UseCase.MEETING,
    )
    job = JobOrchestrator().create_transcription_job(
        media=media,
        language_code="en",
        auto_enqueue=False,
    )
    artifact = MediaProcessingArtifact.objects.create(
        media=media,
        organization=media.organization,
        kind="normalized",
        status=ArtifactStatus.READY,
        storage_backend=media.storage_backend,
        object_key="turing/media/artifacts/test.wav",
        byte_size=123,
        checksum="abc",
        content_type="audio/wav",
        audio_format="wav",
        audio_codec=CANONICAL_CODEC,
    )
    job.ingest_artifact = artifact
    job.save(update_fields=["ingest_artifact", "updated_at"])

    service = TranscriptionService()
    mock_storage = MagicMock()
    mock_storage.supports_remote_fetch.return_value = False
    mock_storage.read_bytes_key.return_value = b"artifact-bytes"
    monkeypatch.setattr(
        "turing.services.media.MediaService",
        lambda: MagicMock(storage=mock_storage),
    )
    request = service._build_request(job)
    assert request.media_bytes == b"artifact-bytes"
    assert request.filename.endswith("-normalized.wav")


@pytest.mark.django_db
def test_prepare_task_chains_submit_on_success(monkeypatch, db):
    from turing.domain.enums import UseCase
    from turing.services.job_orchestrator import JobOrchestrator
    from turing.services.media import MediaService
    from turing.tasks import ingestion as ingestion_tasks

    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(_canonical_wav_bytes()),
        filename="meeting.wav",
        use_case=UseCase.MEETING,
    )
    job = JobOrchestrator().create_transcription_job(
        media=media,
        language_code="en",
        auto_enqueue=False,
    )

    monkeypatch.setattr(
        "turing.services.media_ingestion.MediaIngestionService.prepare_for_job",
        lambda self, job: type(
            "R",
            (),
            {
                "status": IngestStatus.SKIPPED,
                "used_original": True,
                "artifact": None,
            },
        )(),
    )
    scheduled: list[str] = []
    monkeypatch.setattr(
        "turing.tasks.transcription.submit_transcription_job.delay",
        lambda job_id: scheduled.append(job_id),
    )

    result = ingestion_tasks.prepare_media_for_transcription.run(str(job.id))
    assert result == "prepared"
    assert scheduled == [str(job.id)]


@pytest.mark.django_db
def test_prepare_task_does_not_submit_on_ingestion_failure(monkeypatch, db):
    from turing.domain.enums import UseCase
    from turing.services.job_orchestrator import JobOrchestrator
    from turing.services.media import MediaService
    from turing.tasks import ingestion as ingestion_tasks

    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"bad"),
        filename="broken.wav",
        use_case=UseCase.MEETING,
    )
    job = JobOrchestrator().create_transcription_job(
        media=media,
        language_code="en",
        auto_enqueue=False,
    )

    monkeypatch.setattr(
        "turing.services.media_ingestion.MediaIngestionService.prepare_for_job",
        lambda self, job: (_ for _ in ()).throw(
            IngestionError("corrupt audio", code="INGEST_UNREADABLE")
        ),
    )
    scheduled: list[str] = []
    monkeypatch.setattr(
        "turing.tasks.transcription.submit_transcription_job.delay",
        lambda job_id: scheduled.append(job_id),
    )

    result = ingestion_tasks.prepare_media_for_transcription.run(str(job.id))
    job.refresh_from_db()

    assert result == "failed:INGEST_UNREADABLE"
    assert scheduled == []
    assert job.status == JobStatus.FAILED
    assert job.ingest_status == IngestStatus.FAILED
    assert job.ingest_error == "corrupt audio"
