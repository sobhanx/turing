"""Shared export document model built from existing Transcript data."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from turing.services.export.context import ExportVisibility, default_visibility


@dataclass(frozen=True)
class SpeakerTurn:
    speaker_name: str
    text: str
    start_display: str = ""


@dataclass(frozen=True)
class ActionItem:
    task: str
    owner: str = ""
    deadline: str = ""


@dataclass(frozen=True)
class ExportDocument:
    """Normalized payload every exporter renders."""

    transcript_id: str
    project_title: str
    transcript_title: str
    media_filename: str
    organization: str
    language_code: str
    duration_display: str
    generated_at: datetime
    speakers: list[str] = field(default_factory=list)
    turns: list[SpeakerTurn] = field(default_factory=list)
    body_text: str = ""
    rtl: bool = False
    # Cover / meeting metadata
    created_at_display: str = "—"
    provider: str = "—"
    speaker_count: int = 0
    word_count: int = 0
    # Intelligence (existing analyses — presentation only)
    summary: str = ""
    decisions: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    action_items: list[ActionItem] = field(default_factory=list)
    # Admin-resolved presentation
    visibility: ExportVisibility = field(default_factory=default_visibility)
    persian_date: str = "—"
    gregorian_date: str = "—"
    created_persian_date: str = "—"
    created_gregorian_date: str = "—"
    generated_display: str = "—"

    @property
    def download_stem(self) -> str:
        base = (self.media_filename or self.transcript_title or "transcript").strip()
        for ch in r'\/:*?"<>|':
            base = base.replace(ch, "_")
        stem = base.rsplit(".", 1)[0] if "." in base else base
        return (stem or "transcript")[:80]

    def cover_title(self) -> str:
        from turing.services.export import labels as L

        if self.visibility.show_meeting_title:
            return self.transcript_title or L.MEETING_FALLBACK
        return L.REPORT_TITLE

    def cover_rows(self) -> list[tuple[str, str]]:
        from turing.services.export.context import cover_rows_for

        return cover_rows_for(self)

    def meeting_info_rows(self) -> list[tuple[str, str]]:
        from turing.services.export.context import meeting_info_rows_for

        return meeting_info_rows_for(self)

    def turn_timestamp(self, start_display: str) -> str:
        if not self.visibility.show_timeline:
            return ""
        return start_display or ""
