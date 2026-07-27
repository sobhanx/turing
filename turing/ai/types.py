from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TranscriptSegmentInput:
    sequence: int
    text: str
    start_ms: int = 0
    end_ms: int = 0
    speaker_label: str = ""


@dataclass(frozen=True)
class TranscriptInput:
    """Read-only transcript payload for AI providers."""

    transcript_id: str
    language_code: str
    full_text: str
    segments: tuple[TranscriptSegmentInput, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AnalysisResult:
    content: dict[str, Any] | list[Any]
    model_name: str = ""
