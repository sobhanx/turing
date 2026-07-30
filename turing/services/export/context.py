"""
Shared export configuration / visibility layer for PDF and DOCX.

Both exporters must consume this module — do not duplicate section rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from turing.services.export.dates import (
    format_gregorian_date,
    format_gregorian_datetime,
    format_persian_date,
)
from turing.services.export import labels as L

if TYPE_CHECKING:
    from turing.models.export_settings import TranscriptExportSettings
    from turing.services.export.document import ExportDocument


@dataclass(frozen=True)
class ExportVisibility:
    """Resolved section flags shared by all exporters."""

    show_meeting_title: bool = True
    show_persian_date: bool = True
    show_gregorian_date: bool = True
    show_duration: bool = True
    show_speakers: bool = True
    show_full_transcript: bool = True
    show_timeline: bool = True
    show_ai_summary: bool = False
    show_key_topics: bool = False
    show_action_items: bool = False
    show_decisions: bool = False
    show_keywords: bool = False
    show_provider: bool = False

    @property
    def any_ai_section(self) -> bool:
        return any(
            (
                self.show_ai_summary,
                self.show_key_topics,
                self.show_action_items,
                self.show_decisions,
                self.show_keywords,
            )
        )


def default_visibility() -> ExportVisibility:
    """Platform defaults (matches model field defaults)."""
    return ExportVisibility()


def visibility_from_settings(settings: TranscriptExportSettings) -> ExportVisibility:
    return ExportVisibility(
        show_meeting_title=settings.show_meeting_title,
        show_persian_date=settings.show_persian_date,
        show_gregorian_date=settings.show_gregorian_date,
        show_duration=settings.show_duration,
        show_speakers=settings.show_speakers,
        show_full_transcript=settings.show_full_transcript,
        show_timeline=settings.show_timeline,
        show_ai_summary=settings.show_ai_summary,
        show_key_topics=settings.show_key_topics,
        show_action_items=settings.show_action_items,
        show_decisions=settings.show_decisions,
        show_keywords=settings.show_keywords,
        show_provider=settings.show_provider,
    )


def cover_rows_for(document: ExportDocument) -> list[tuple[str, str]]:
    vis = document.visibility
    rows: list[tuple[str, str]] = [(L.LABEL_ORGANIZATION, document.organization)]
    if vis.show_persian_date:
        rows.append((L.LABEL_PERSIAN_DATE, document.persian_date))
    if vis.show_gregorian_date:
        rows.append((L.LABEL_GREGORIAN_DATE, document.gregorian_date))
    rows.append((L.LABEL_LANGUAGE, document.language_code or "—"))
    if vis.show_duration:
        rows.append((L.LABEL_DURATION, document.duration_display))
    if vis.show_speakers:
        rows.append((L.LABEL_SPEAKERS, str(document.speaker_count)))
    rows.append((L.LABEL_WORDS, str(document.word_count)))
    return rows


def meeting_info_rows_for(document: ExportDocument) -> list[tuple[str, str]]:
    vis = document.visibility
    rows: list[tuple[str, str]] = []
    if vis.show_meeting_title:
        rows.append((L.LABEL_MEETING_TITLE, document.transcript_title))
    rows.append((L.LABEL_ORGANIZATION, document.organization))
    if vis.show_persian_date:
        rows.append((L.LABEL_PERSIAN_DATE, document.created_persian_date))
    if vis.show_gregorian_date:
        rows.append((L.LABEL_GREGORIAN_DATE, document.created_gregorian_date))
    if vis.show_duration:
        rows.append((L.LABEL_DURATION, document.duration_display))
    rows.append((L.LABEL_LANGUAGE, document.language_code or "—"))
    if vis.show_provider:
        rows.append((L.LABEL_PROVIDER, document.provider or "—"))
    if vis.show_speakers and document.speakers:
        rows.append((L.LABEL_SPEAKERS, ", ".join(document.speakers)))
    return rows


def apply_settings_to_document(
    document: ExportDocument,
    *,
    settings: TranscriptExportSettings | None = None,
    created_at: datetime | None = None,
) -> ExportDocument:
    """
    Attach resolved visibility + Tehran/Jalali dates to an ExportDocument.

    Call once in ExportService so PDF and DOCX share identical rules.
    """
    from dataclasses import replace

    if settings is None:
        from turing.models.export_settings import TranscriptExportSettings

        settings = TranscriptExportSettings.resolve_for_organization(None)

    visibility = visibility_from_settings(settings)
    generated = document.generated_at
    return replace(
        document,
        visibility=visibility,
        persian_date=format_persian_date(generated),
        gregorian_date=format_gregorian_date(generated),
        created_persian_date=format_persian_date(created_at),
        created_gregorian_date=format_gregorian_date(created_at),
        generated_display=format_gregorian_datetime(generated),
        created_at_display=_created_at_combined(
            visibility,
            persian=format_persian_date(created_at),
            gregorian=format_gregorian_date(created_at),
        ),
    )


def _created_at_combined(
    visibility: ExportVisibility, *, persian: str, gregorian: str
) -> str:
    parts: list[str] = []
    if visibility.show_persian_date:
        parts.append(persian)
    if visibility.show_gregorian_date:
        parts.append(gregorian)
    return " · ".join(parts) if parts else "—"
