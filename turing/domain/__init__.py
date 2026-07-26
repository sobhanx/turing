from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class DomainEvent:
    name: str
    payload: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime | None = None


def job_queued(job_id: str) -> DomainEvent:
    return DomainEvent(name="processing_job.queued", payload={"job_id": job_id})


def job_succeeded(job_id: str, transcript_id: str) -> DomainEvent:
    return DomainEvent(
        name="processing_job.succeeded",
        payload={"job_id": job_id, "transcript_id": transcript_id},
    )


def job_failed(job_id: str, error_code: str, message: str) -> DomainEvent:
    return DomainEvent(
        name="processing_job.failed",
        payload={"job_id": job_id, "error_code": error_code, "message": message},
    )


def transcript_revised(transcript_id: str, revision_number: int) -> DomainEvent:
    return DomainEvent(
        name="transcript.revised",
        payload={"transcript_id": transcript_id, "revision_number": revision_number},
    )
