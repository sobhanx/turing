from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass

from django.db import transaction

from turing.conf import get_turing_settings
from turing.domain.enums import ArtifactKind, ArtifactStatus, IngestStatus, SourceType
from turing.domain.exceptions import IngestionError, ValidationError
from turing.domain.ingestion import (
    AudioProbeResult,
    is_stt_compatible,
    needs_normalization,
)
from turing.media.inspection import AudioInspectionService
from turing.media.normalization import AudioNormalizationService
from turing.models import MediaAsset, MediaProcessingArtifact, ProcessingJob
from turing.services.media import MediaService
from turing.storage.spool import CHUNK_SIZE, spool_upload

logger = logging.getLogger(__name__)

_PROBE_FAILURE_CODES = frozenset(
    {
        "ffprobe_not_available",
        "ffprobe_failed",
        "invalid_ffprobe_json",
    }
)


@dataclass(frozen=True)
class IngestionResult:
    artifact: MediaProcessingArtifact | None
    probe: AudioProbeResult | None
    status: IngestStatus
    used_original: bool


class MediaIngestionService:
    """Inspect and normalize media before STT without mutating the original asset."""

    def __init__(
        self,
        *,
        media_service: MediaService | None = None,
        inspector: AudioInspectionService | None = None,
        normalizer: AudioNormalizationService | None = None,
    ) -> None:
        self.media_service = media_service or MediaService()
        self.inspector = inspector or AudioInspectionService()
        self.normalizer = normalizer or AudioNormalizationService(inspector=self.inspector)

    def prepare_for_job(self, job: ProcessingJob) -> IngestionResult:
        media = job.media
        if media.source_type == SourceType.URL:
            logger.info("Skipping ingestion for URL media %s", media.id)
            self._mark_ingest_skipped(job)
            return IngestionResult(
                artifact=None,
                probe=None,
                status=IngestStatus.SKIPPED,
                used_original=True,
            )

        settings = get_turing_settings()
        if not settings.normalization_enabled:
            self._mark_ingest_skipped(job)
            return IngestionResult(
                artifact=None,
                probe=None,
                status=IngestStatus.SKIPPED,
                used_original=True,
            )

        existing = self._get_ready_artifact(media)
        if existing:
            self._apply_probe_to_media(media, self._probe_from_artifact(existing))
            self._set_job_duration(job, existing.duration_ms)
            job.ingest_artifact = existing
            self._mark_ingest_succeeded(job)
            return IngestionResult(
                artifact=existing,
                probe=self._probe_from_artifact(existing),
                status=IngestStatus.SUCCEEDED,
                used_original=False,
            )

        return self._prepare_from_storage(job, media)

    def _prepare_from_storage(self, job: ProcessingJob, media: MediaAsset) -> IngestionResult:
        settings = get_turing_settings()
        with self._local_media_path(media) as local_path:
            probe = self.inspector.probe(local_path)
            if not probe.readable:
                raise self._probe_failure(probe)

            self._apply_probe_to_media(media, probe)
            self._enforce_max_duration(probe.duration_ms, settings.max_duration_ms)
            self._set_job_duration(job, probe.duration_ms)

            if is_stt_compatible(probe):
                skipped = self._create_skipped_artifact(media, probe)
                job.ingest_artifact = None
                self._mark_ingest_succeeded(
                    job,
                    update_fields=["expected_duration_ms", "ingest_artifact"],
                )
                return IngestionResult(
                    artifact=skipped,
                    probe=probe,
                    status=IngestStatus.SUCCEEDED,
                    used_original=True,
                )

            if not needs_normalization(probe):
                raise IngestionError(
                    "Audio is readable but not compatible with STT and cannot be normalized.",
                    code="INGEST_INCOMPATIBLE",
                )

            normalized_path = self._normalize_to_temp(local_path)
            if normalized_path is None:
                raise IngestionError(
                    "Audio normalization failed.",
                    code="INGEST_NORMALIZE_FAILED",
                )

            try:
                artifact = self._persist_normalized_artifact(media, normalized_path, source_probe=probe)
            finally:
                if os.path.exists(normalized_path):
                    os.unlink(normalized_path)

            job.ingest_artifact = artifact
            self._mark_ingest_succeeded(
                job,
                update_fields=["ingest_artifact", "expected_duration_ms"],
            )
            return IngestionResult(
                artifact=artifact,
                probe=probe,
                status=IngestStatus.SUCCEEDED,
                used_original=False,
            )

    def _probe_failure(self, probe: AudioProbeResult) -> IngestionError:
        message = probe.error_message or "Audio file is unreadable."
        if message in _PROBE_FAILURE_CODES or message.startswith("invalid_ffprobe_json"):
            code = "INGEST_PROBE_FAILED"
        else:
            code = "INGEST_UNREADABLE"
        return IngestionError(message, code=code)

    def _normalize_to_temp(self, local_path: str) -> str | None:
        fd, output_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        result = self.normalizer.normalize(local_path, output_path)
        if not result.success:
            logger.warning(
                "Normalization failed for %s (%s).",
                local_path,
                result.error_message,
            )
            if os.path.exists(output_path):
                os.unlink(output_path)
            return None
        return output_path

    @transaction.atomic
    def _persist_normalized_artifact(
        self,
        media: MediaAsset,
        normalized_path: str,
        *,
        source_probe: AudioProbeResult,
    ) -> MediaProcessingArtifact:
        out_probe = self.inspector.probe(normalized_path)
        if not out_probe.readable:
            raise IngestionError(
                out_probe.error_message or "Normalized audio is unreadable.",
                code="INGEST_NORMALIZE_FAILED",
            )

        with open(normalized_path, "rb") as handle:
            with spool_upload(handle, max_bytes=get_turing_settings().max_upload_bytes) as spool:
                with spool.open("rb") as readable:
                    object_key = self.media_service.storage.save_upload(
                        filename=f"{media.id}-normalized.wav",
                        content=readable,
                        content_type="audio/wav",
                        key_prefix="turing/media/artifacts",
                    )
                checksum = spool.checksum
                byte_size = spool.size

        settings = get_turing_settings()
        artifact = MediaProcessingArtifact.objects.create(
            media=media,
            organization=media.organization,
            kind=ArtifactKind.NORMALIZED,
            status=ArtifactStatus.READY,
            storage_backend=settings.storage_backend,
            object_key=object_key,
            byte_size=byte_size,
            checksum=checksum,
            content_type="audio/wav",
            audio_format=out_probe.format or "wav",
            audio_codec=out_probe.codec or "pcm_s16le",
            duration_ms=out_probe.duration_ms,
            sample_rate_hz=out_probe.sample_rate_hz,
            channels=out_probe.channels,
            probe_metadata={
                "source_probe": source_probe.raw,
                "output_probe": out_probe.raw,
            },
        )
        return artifact

    @transaction.atomic
    def _create_skipped_artifact(
        self,
        media: MediaAsset,
        probe: AudioProbeResult,
    ) -> MediaProcessingArtifact:
        return MediaProcessingArtifact.objects.create(
            media=media,
            organization=media.organization,
            kind=ArtifactKind.NORMALIZED,
            status=ArtifactStatus.SKIPPED,
            storage_backend=media.storage_backend,
            object_key=media.object_key,
            byte_size=media.byte_size,
            checksum=media.checksum,
            content_type=media.content_type,
            audio_format=probe.format,
            audio_codec=probe.codec,
            duration_ms=probe.duration_ms,
            sample_rate_hz=probe.sample_rate_hz,
            channels=probe.channels,
            probe_metadata={"probe": probe.raw, "reason": "already_compatible"},
        )

    def _get_ready_artifact(self, media: MediaAsset) -> MediaProcessingArtifact | None:
        return (
            MediaProcessingArtifact.objects.filter(
                media=media,
                kind=ArtifactKind.NORMALIZED,
                status=ArtifactStatus.READY,
            )
            .order_by("-created_at")
            .first()
        )

    def _apply_probe_to_media(self, media: MediaAsset, probe: AudioProbeResult) -> None:
        updates: list[str] = []
        if probe.duration_ms is not None and media.duration_ms != probe.duration_ms:
            media.duration_ms = probe.duration_ms
            updates.append("duration_ms")
        if probe.sample_rate_hz is not None and media.sample_rate_hz != probe.sample_rate_hz:
            media.sample_rate_hz = probe.sample_rate_hz
            updates.append("sample_rate_hz")
        if probe.channels is not None and media.channels != probe.channels:
            media.channels = probe.channels
            updates.append("channels")
        if probe.format and media.audio_format != probe.format:
            media.audio_format = probe.format
            updates.append("audio_format")
        if probe.codec and media.audio_codec != probe.codec:
            media.audio_codec = probe.codec
            updates.append("audio_codec")
        if updates:
            updates.append("updated_at")
            media.save(update_fields=updates)

    def _set_job_duration(self, job: ProcessingJob, duration_ms: int | None) -> None:
        if duration_ms is not None:
            job.expected_duration_ms = duration_ms

    def _enforce_max_duration(self, duration_ms: int | None, max_duration_ms: int) -> None:
        if not max_duration_ms or max_duration_ms <= 0:
            return
        if duration_ms is not None and duration_ms > max_duration_ms:
            raise ValidationError(
                f"Audio duration {duration_ms}ms exceeds configured maximum {max_duration_ms}ms."
            )

    def _mark_ingest_succeeded(
        self,
        job: ProcessingJob,
        *,
        update_fields: list[str] | None = None,
    ) -> None:
        job.ingest_status = IngestStatus.SUCCEEDED
        job.ingest_error = ""
        fields = list(update_fields or [])
        fields.extend(["ingest_status", "ingest_error", "updated_at"])
        job.save(update_fields=fields)

    def _mark_ingest_skipped(self, job: ProcessingJob) -> None:
        job.ingest_status = IngestStatus.SKIPPED
        job.ingest_error = ""
        job.save(update_fields=["ingest_status", "ingest_error", "updated_at"])

    def _probe_from_artifact(self, artifact: MediaProcessingArtifact) -> AudioProbeResult:
        return AudioProbeResult(
            format=artifact.audio_format,
            codec=artifact.audio_codec,
            duration_ms=artifact.duration_ms,
            sample_rate_hz=artifact.sample_rate_hz,
            channels=artifact.channels,
            bitrate=None,
            raw=artifact.probe_metadata or {},
            readable=artifact.status in {ArtifactStatus.READY, ArtifactStatus.SKIPPED},
        )

    def _local_media_path(self, media: MediaAsset):
        return _MediaPathContext(self.media_service, media)


class _MediaPathContext:
    def __init__(self, media_service: MediaService, media: MediaAsset) -> None:
        self.media_service = media_service
        self.media = media
        self._temp_path: str | None = None

    def __enter__(self) -> str:
        self._temp_path = self._materialize_to_temp()
        return self._temp_path

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._temp_path and os.path.exists(self._temp_path):
            try:
                os.unlink(self._temp_path)
            except OSError:
                pass
        self._temp_path = None

    def _materialize_to_temp(self) -> str:
        settings = get_turing_settings()
        fd, path = tempfile.mkstemp(prefix="turing-ingest-", suffix=".bin")
        os.close(fd)
        try:
            with open(path, "wb") as out:
                if not self.media_service.storage.exists(self.media):
                    if self.media.file:
                        self.media.file.open("rb")
                        try:
                            self._copy_limited(out, self.media.file, settings.max_upload_bytes)
                        finally:
                            self.media.file.close()
                    else:
                        raise FileNotFoundError(f"MediaAsset {self.media.id} has no stored file.")
                else:
                    with self.media_service.storage.open(self.media) as handle:
                        self._copy_limited(out, handle, settings.max_upload_bytes)
            return path
        except Exception:
            if os.path.exists(path):
                os.unlink(path)
            raise

    @staticmethod
    def _copy_limited(out, source, max_bytes: int) -> None:
        size = 0
        while True:
            chunk = source.read(CHUNK_SIZE)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise ValidationError(
                    f"Media exceeds max upload size ({max_bytes} bytes)."
                )
            out.write(chunk)
