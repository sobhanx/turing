"""ExportService — builds ExportDocument and streams format-specific bytes."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Iterator

from django.contrib.auth.models import AbstractBaseUser

from turing.auth.tenancy import assert_organization_access
from turing.domain.exceptions import NotFoundError, ValidationError
from turing.models import Transcript
from turing.models.export_settings import TranscriptExportSettings
from turing.services.export.base import BaseExporter, ExportRegistry
from turing.services.export.context import apply_settings_to_document
from turing.services.export.document import ActionItem, ExportDocument, SpeakerTurn
from turing.services.export import labels as export_labels
from turing.services.export.text import is_rtl_language
from turing.services.transcript import TranscriptService

# Register built-in exporters on import.
from turing.services.export import docx_exporter as _docx  # noqa: F401
from turing.services.export import pdf as _pdf  # noqa: F401

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _format_duration_ms(duration_ms: int | None) -> str:
    if duration_ms is None:
        return "—"
    total_seconds = max(0, int(duration_ms)) // 1000
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _format_timestamp_ms(start_ms: int | None) -> str:
    if start_ms is None:
        return ""
    total_seconds = max(0, int(start_ms)) // 1000
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _format_dt(value) -> str:
    if value is None:
        return "—"
    if getattr(value, "tzinfo", None) is not None:
        value = value.astimezone(timezone.utc)
    return value.strftime("%Y-%m-%d %H:%M UTC")


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
        # Source of truth: TranscriptSegment rows in UI/API order (sequence).
        # Do not merge, rewrite, or substitute full_text / AI fields.
        turns = self._build_timed_turns(transcript)
        body = self.transcripts.format_export_body(
            transcript, merge_consecutive=False
        )
        self._log_export_segments(transcript, turns)

        media_name = ""
        duration_ms = None
        if media is not None:
            media_name = (
                (media.original_filename or "").strip()
                or (media.object_key or "").strip()
                or str(media.pk)
            )
            duration_ms = media.duration_ms

        project_title = (org.name if org is not None else "") or export_labels.DEFAULT_PROJECT
        transcript_title = media_name or f"{export_labels.SECTION_TRANSCRIPT} {transcript.pk}"

        provider = "—"
        job = getattr(transcript, "job", None)
        if job is not None and getattr(job, "provider_code", None):
            provider = str(job.provider_code)

        word_count = int(getattr(transcript, "word_count", 0) or 0)
        if word_count <= 0 and transcript.full_text:
            word_count = len(transcript.full_text.split())

        settings = TranscriptExportSettings.resolve_for_organization(
            org if org is not None else None
        )

        if (
            settings.show_ai_summary
            or settings.show_key_topics
            or settings.show_action_items
            or settings.show_decisions
            or settings.show_keywords
        ):
            summary, decisions, topics, keywords, action_items = self._load_intelligence(
                transcript
            )
            if not settings.show_ai_summary:
                summary = ""
            if not settings.show_decisions:
                decisions = []
            if not settings.show_key_topics:
                topics = []
            if not settings.show_keywords:
                keywords = []
            if not settings.show_action_items:
                action_items = []
        else:
            summary, decisions, topics, keywords, action_items = (
                "",
                [],
                [],
                [],
                [],
            )

        document = ExportDocument(
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
            created_at_display=_format_dt(getattr(transcript, "created_at", None)),
            provider=provider,
            speaker_count=len(speakers),
            word_count=word_count,
            summary=summary,
            decisions=decisions,
            topics=topics,
            keywords=keywords,
            action_items=action_items,
        )
        return apply_settings_to_document(
            document,
            settings=settings,
            created_at=getattr(transcript, "created_at", None),
        )

    def _build_timed_turns(self, transcript: Transcript) -> list[SpeakerTurn]:
        """
        One export turn per TranscriptSegment — same order as the Segments UI.

        Does not merge consecutive speakers, rewrite text, or use full_text /
        AI analysis for the dialogue body.
        """
        pending: list[SpeakerTurn] = []
        for seg in transcript.segments.select_related("speaker").order_by(
            "sequence", "start_ms"
        ):
            # Preserve segment text exactly (only skip blank rows).
            text = seg.text if seg.text is not None else ""
            if not str(text).strip():
                continue
            name = ""
            if seg.speaker_id and seg.speaker is not None:
                name = seg.speaker.resolved_name
            pending.append(
                SpeakerTurn(
                    speaker_name=name,
                    text=str(text),
                    start_display=_format_timestamp_ms(seg.start_ms),
                    sequence=int(seg.sequence),
                    segment_id=str(seg.pk),
                )
            )
        return pending

    def _log_export_segments(
        self, transcript: Transcript, turns: list[SpeakerTurn]
    ) -> None:
        """Log the exact segment payload passed to exporters (debug / verify)."""
        payload = {
            "transcript_id": str(transcript.pk),
            "language_code": transcript.language_code or "",
            "source": "transcript_segments",
            "segment_count": len(turns),
            "segments": [
                {
                    "segment_id": turn.segment_id,
                    "sequence": turn.sequence,
                    "speaker": turn.speaker_name,
                    "start_display": turn.start_display,
                    "text": turn.text,
                }
                for turn in turns
            ],
        }
        logger.info(
            "export_transcript_segments_payload %s",
            json.dumps(payload, ensure_ascii=False),
        )

    def _load_intelligence(
        self, transcript: Transcript
    ) -> tuple[str, list[str], list[str], list[str], list[ActionItem]]:
        """Read existing analysis rows without regenerating AI content."""
        summary_text = ""
        decisions: list[str] = []
        topics: list[str] = []
        keywords: list[str] = []
        action_items: list[ActionItem] = []

        try:
            from turing.services.speech_center import SpeechCenterService

            intel = SpeechCenterService().get_latest_intelligence(transcript)
        except Exception:  # noqa: BLE001 — export must not fail if analyses missing
            return summary_text, decisions, topics, keywords, action_items

        summary_payload = intel.get("summary")
        if isinstance(summary_payload, dict):
            summary_text = str(summary_payload.get("summary") or "").strip()
            raw_points = summary_payload.get("main_points") or []
            if isinstance(raw_points, list):
                decisions = [str(p).strip() for p in raw_points if str(p).strip()]

        topics_payload = intel.get("topics")
        if isinstance(topics_payload, list):
            topics = [str(t).strip() for t in topics_payload if str(t).strip()]
            keywords = list(topics)

        actions_payload = intel.get("action_items")
        if isinstance(actions_payload, list):
            for item in actions_payload:
                if not isinstance(item, dict):
                    continue
                task = str(item.get("task") or "").strip()
                if not task:
                    continue
                owner = str(item.get("owner") or "").strip()
                deadline = str(item.get("deadline") or "").strip()
                action_items.append(
                    ActionItem(task=task, owner=owner, deadline=deadline)
                )

        return summary_text, decisions, topics, keywords, action_items

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
