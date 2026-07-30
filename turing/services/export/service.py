"""ExportService — builds ExportDocument and streams format-specific bytes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Iterator

from django.contrib.auth.models import AbstractBaseUser

from turing.auth.tenancy import assert_organization_access
from turing.domain.exceptions import NotFoundError, ValidationError
from turing.models import Transcript
from turing.services.export.base import BaseExporter, ExportRegistry
from turing.services.export.document import ExportDocument, SpeakerTurn
from turing.services.export.text import is_rtl_language
from turing.services.transcript import TranscriptService

# Register built-in exporters on import.
from turing.services.export import docx_exporter as _docx  # noqa: F401
from turing.services.export import pdf as _pdf  # noqa: F401

if TYPE_CHECKING:
    pass


def _format_duration_ms(duration_ms: int | None) -> str:
    if duration_ms is None:
        return "—"
    total_seconds = max(0, int(duration_ms)) // 1000
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"

@dataclass(frozen=True)
class ExportResult:
    format_code: str
    content_type: str
    filename: str
    chunks: Iterator[bytes]

    def __iter__(self) -> Iterator[bytes]:
        return iter(self.chunks)


class ExportService:
    """
    On-demand transcript export.

    Does not persist generated files. Reuses ``TranscriptService`` for dialogue
    formatting and tenancy checks via organization capability.
    """

    def __init__(self, transcript_service: TranscriptService | None = None) -> None:
        self.transcripts = transcript_service or TranscriptService()

    def supported_formats(self) -> list[dict[str, str]]:
        formats = []
        for code in ExportRegistry.supported_formats():
            exporter = ExportRegistry.get(code)
            formats.append(
                {
                    "code": exporter.format_code,
                    "label": exporter.label or exporter.format_code.upper(),
                    "extension": exporter.file_extension,
                    "content_type": exporter.content_type,
                }
            )
        return formats

    def export(
        self,
        transcript_id,
        format_code: str,
        *,
        user: AbstractBaseUser | None = None,
        chunk_size: int = 64 * 1024,
    ) -> ExportResult:
        transcript = self._load_authorized(transcript_id, user=user)
        document = self.build_document(transcript)
        exporter = ExportRegistry.get(format_code)
        filename = f"{document.download_stem}.{exporter.file_extension}"
        return ExportResult(
            format_code=exporter.format_code,
            content_type=exporter.content_type,
            filename=filename,
            chunks=exporter.iter_chunks(document, chunk_size=chunk_size),
        )

    def export_transcript(
        self,
        transcript: Transcript,
        format_code: str,
        *,
        user: AbstractBaseUser | None = None,
        chunk_size: int = 64 * 1024,
    ) -> ExportResult:
        if user is not None:
            assert_organization_access(
                user, transcript.organization, capability="view_transcript"
            )
        document = self.build_document(transcript)
        exporter = ExportRegistry.get(format_code)
        filename = f"{document.download_stem}.{exporter.file_extension}"
        return ExportResult(
            format_code=exporter.format_code,
            content_type=exporter.content_type,
            filename=filename,
            chunks=exporter.iter_chunks(document, chunk_size=chunk_size),
        )

    def build_document(self, transcript: Transcript) -> ExportDocument:
        # Ensure related rows are available (segments + speakers).
        if transcript.pk:
            try:
                transcript = self.transcripts.get(transcript.pk)
            except NotFoundError:
                pass

        media = transcript.media
        org = transcript.organization
        language = (transcript.language_code or "").strip()
        rtl = is_rtl_language(language)

        speakers = [
            s.resolved_name
            for s in transcript.speakers.all().order_by("speaker_label")
        ]
        turns = [
            SpeakerTurn(speaker_name=name, text=text)
            for name, text in self.transcripts.iter_speaker_turns(transcript)
            if text
        ]
        body = self.transcripts.format_export_body(transcript)

        media_name = ""
        duration_ms = None
        if media is not None:
            media_name = (
                (media.original_filename or "").strip()
                or (media.object_key or "").strip()
                or str(media.pk)
            )
            duration_ms = media.duration_ms

        project_title = (org.name if org is not None else "") or "Speech Center"
        transcript_title = media_name or f"Transcript {transcript.pk}"

        return ExportDocument(
            transcript_id=str(transcript.pk),
            project_title=project_title,
            transcript_title=transcript_title,
            media_filename=media_name or "—",
            organization=org.name if org is not None else "—",
            language_code=language,
            duration_display=_format_duration_ms(duration_ms),
            generated_at=datetime.now(timezone.utc),
            speakers=speakers,
            turns=turns,
            body_text=body,
            rtl=rtl,
        )

    def _load_authorized(
        self,
        transcript_id,
        *,
        user: AbstractBaseUser | None,
    ) -> Transcript:
        try:
            transcript = self.transcripts.get(transcript_id)
        except NotFoundError:
            raise
        if user is not None:
            assert_organization_access(
                user, transcript.organization, capability="view_transcript"
            )
        return transcript


def get_exporter(format_code: str) -> BaseExporter:
    return ExportRegistry.get(format_code)


def ensure_supported_format(format_code: str) -> str:
    code = (format_code or "").strip().lower()
    if code not in ExportRegistry.supported_formats():
        raise ValidationError(
            f"Unsupported export format '{format_code}'. "
            f"Supported: {', '.join(ExportRegistry.supported_formats())}."
        )
    return code
