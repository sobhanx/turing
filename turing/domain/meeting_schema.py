from __future__ import annotations

"""
Vendor-free DTOs for meeting connector normalization.

Connectors should eventually map vendor payloads into these shapes, then into
``Meeting`` / ``Recording`` / ``MediaAsset`` via ``MeetingService``.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class NormalizedMeeting:
    """Provider-agnostic meeting session descriptor."""

    external_id: str
    provider: str
    title: str = ""
    status: str = "unknown"
    scheduled_at: datetime | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    participants: list[Any] = field(default_factory=list)
    host_external_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedRecording:
    """Provider-agnostic recording file descriptor."""

    external_id: str
    provider: str
    meeting_external_id: str
    source_url: str = ""
    filename: str = ""
    duration_ms: int | None = None
    status: str = "discovered"
    metadata: dict[str, Any] = field(default_factory=dict)
