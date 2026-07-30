"""Shared export document model built from existing Transcript data."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class SpeakerTurn:
    speaker_name: str
    text: str


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

    @property
    def download_stem(self) -> str:
        base = (self.media_filename or self.transcript_title or "transcript").strip()
        for ch in r'\/:*?"<>|':
            base = base.replace(ch, "_")
        stem = base.rsplit(".", 1)[0] if "." in base else base
        return (stem or "transcript")[:80]
