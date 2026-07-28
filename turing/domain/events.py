from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


class EventName:
    """Canonical Phase 4 event names."""

    MEDIA_CREATED = "media.created"
    JOB_COMPLETED = "job.completed"
    TRANSCRIPT_CREATED = "transcript.created"
    ANALYSIS_COMPLETED = "analysis.completed"
    CONNECTOR_SYNC_STARTED = "connector.sync.started"
    CONNECTOR_SYNC_COMPLETED = "connector.sync.completed"
    CONNECTOR_SYNC_FAILED = "connector.sync.failed"


# Names allowed on outbound webhook subscriptions (plus ``*`` for all).
SUPPORTED_OUTBOUND_EVENT_NAMES: frozenset[str] = frozenset(
    {
        EventName.MEDIA_CREATED,
        EventName.JOB_COMPLETED,
        EventName.TRANSCRIPT_CREATED,
        EventName.ANALYSIS_COMPLETED,
        EventName.CONNECTOR_SYNC_STARTED,
        EventName.CONNECTOR_SYNC_COMPLETED,
        EventName.CONNECTOR_SYNC_FAILED,
    }
)


@dataclass(frozen=True)
class DomainEvent:
    """Lightweight notification for host integrations (not a Celery replacement)."""

    name: str
    payload: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.occurred_at is None:
            object.__setattr__(self, "occurred_at", datetime.now(timezone.utc))


def media_created(
    *,
    media_id: str,
    organization_id: int,
    external_references: list[dict[str, str]] | None = None,
) -> DomainEvent:
    return DomainEvent(
        name=EventName.MEDIA_CREATED,
        payload={
            "media_id": str(media_id),
            "organization_id": organization_id,
            "external_references": list(external_references or []),
        },
    )


def job_completed(
    *,
    job_id: str,
    organization_id: int,
    media_id: str | None = None,
    transcript_id: str | None = None,
    external_references: list[dict[str, str]] | None = None,
) -> DomainEvent:
    payload: dict[str, Any] = {
        "job_id": str(job_id),
        "organization_id": organization_id,
        "external_references": list(external_references or []),
    }
    if media_id is not None:
        payload["media_id"] = str(media_id)
    if transcript_id is not None:
        payload["transcript_id"] = str(transcript_id)
    return DomainEvent(name=EventName.JOB_COMPLETED, payload=payload)


def transcript_created(
    *,
    transcript_id: str,
    organization_id: int,
    media_id: str | None = None,
    job_id: str | None = None,
    external_references: list[dict[str, str]] | None = None,
) -> DomainEvent:
    payload: dict[str, Any] = {
        "transcript_id": str(transcript_id),
        "organization_id": organization_id,
        "external_references": list(external_references or []),
    }
    if media_id is not None:
        payload["media_id"] = str(media_id)
    if job_id is not None:
        payload["job_id"] = str(job_id)
    return DomainEvent(name=EventName.TRANSCRIPT_CREATED, payload=payload)


def analysis_completed(
    *,
    transcript_id: str,
    organization_id: int,
    analysis_ids: list[str],
    analysis_types: list[str],
    provider: str = "",
    external_references: list[dict[str, str]] | None = None,
) -> DomainEvent:
    return DomainEvent(
        name=EventName.ANALYSIS_COMPLETED,
        payload={
            "transcript_id": str(transcript_id),
            "organization_id": organization_id,
            "analysis_ids": [str(i) for i in analysis_ids],
            "analysis_types": list(analysis_types),
            "provider": provider or "",
            "external_references": list(external_references or []),
        },
    )


def connector_sync_started(
    *,
    sync_job_id: str,
    installation_id: str,
    organization_id: int,
    connector_type: str,
) -> DomainEvent:
    return DomainEvent(
        name=EventName.CONNECTOR_SYNC_STARTED,
        payload={
            "sync_job_id": str(sync_job_id),
            "installation_id": str(installation_id),
            "organization_id": organization_id,
            "connector_type": connector_type,
        },
    )


def connector_sync_completed(
    *,
    sync_job_id: str,
    installation_id: str,
    organization_id: int,
    connector_type: str,
    records_processed: int = 0,
) -> DomainEvent:
    return DomainEvent(
        name=EventName.CONNECTOR_SYNC_COMPLETED,
        payload={
            "sync_job_id": str(sync_job_id),
            "installation_id": str(installation_id),
            "organization_id": organization_id,
            "connector_type": connector_type,
            "records_processed": int(records_processed),
        },
    )


def connector_sync_failed(
    *,
    sync_job_id: str,
    installation_id: str,
    organization_id: int,
    connector_type: str,
    error_code: str = "",
) -> DomainEvent:
    return DomainEvent(
        name=EventName.CONNECTOR_SYNC_FAILED,
        payload={
            "sync_job_id": str(sync_job_id),
            "installation_id": str(installation_id),
            "organization_id": organization_id,
            "connector_type": connector_type,
            "error_code": error_code or "",
        },
    )


# Legacy helpers (pre-Phase 4 names). Prefer EventName factories above.
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
