from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TranscriptionRequest:
    """Provider-agnostic transcription request."""

    media_url: str | None = None
    media_path: str | None = None
    media_bytes: bytes | None = None
    filename: str = "audio"
    content_type: str = "application/octet-stream"
    language_code: str = ""
    diarization: bool = True
    operating_point: str = "enhanced"
    extra_options: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderJobHandle:
    external_job_id: str
    provider_code: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderJobStatus:
    external_job_id: str
    state: str  # running|succeeded|failed
    message: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.state in {"succeeded", "failed"}

    @property
    def is_success(self) -> bool:
        return self.state == "succeeded"


@dataclass
class NormalizedWord:
    text: str
    start_ms: int
    end_ms: int
    confidence: float | None = None
    speaker_label: str | None = None


@dataclass
class NormalizedSegment:
    sequence: int
    text: str
    start_ms: int
    end_ms: int
    confidence: float | None = None
    speaker_label: str | None = None
    words: list[NormalizedWord] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedSpeaker:
    label: str
    display_name: str = ""
    confidence: float | None = None
    external_speaker_id: str = ""


@dataclass
class NormalizedTranscript:
    language_code: str = ""
    full_text: str = ""
    confidence_avg: float | None = None
    speakers: list[NormalizedSpeaker] = field(default_factory=list)
    segments: list[NormalizedSegment] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
