from __future__ import annotations

# Re-export event helpers for ``from turing.domain import DomainEvent``.
from turing.domain.events import (  # noqa: F401
    DomainEvent,
    EventName,
    analysis_completed,
    job_completed,
    job_failed,
    job_queued,
    job_succeeded,
    media_created,
    transcript_created,
    transcript_revised,
)

__all__ = [
    "DomainEvent",
    "EventName",
    "media_created",
    "job_completed",
    "transcript_created",
    "analysis_completed",
    "job_queued",
    "job_succeeded",
    "job_failed",
    "transcript_revised",
]
