"""Presentation helpers for Speech Center UI (no business logic)."""

from __future__ import annotations

from turing.domain.enums import IngestStatus, JobStatus
from turing.models import ProcessingJob
from turing.services.transcription import PIPELINE_META_KEY


# Demo labels requested by product — mapped from existing job/ingest state.
STATUS_QUEUED = "Queued"
STATUS_PREPARING = "Preparing"
STATUS_UPLOADING = "Uploading"
STATUS_SUBMITTED = "Submitted"
STATUS_PROCESSING = "Processing"
STATUS_COMPLETED = "Completed"
STATUS_RETRY_SCHEDULED = "Retry Scheduled"
STATUS_FAILED = "Failed"
STATUS_CANCELLED = "Cancelled"


def format_duration_ms(duration_ms: int | None) -> str:
    """Format media duration for display; presentation only."""
    if duration_ms is None:
        return "—"
    total_seconds = max(0, int(duration_ms)) // 1000
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def job_display_status(job: ProcessingJob) -> tuple[str, str]:
    """
    Return ``(label, css_modifier)`` for a processing job badge.

    Pure presentation mapping over existing ``status`` / ``ingest_status`` /
    pipeline metadata — does not change job state.
    """
    status = job.status
    if status == JobStatus.FAILED:
        return STATUS_FAILED, "failed"
    if status == JobStatus.SUCCEEDED:
        return STATUS_COMPLETED, "completed"
    if status == JobStatus.CANCELLED:
        return STATUS_CANCELLED, "cancelled"
    if status in {JobStatus.PENDING, JobStatus.QUEUED}:
        if getattr(job, "attempt_count", 0) and job.attempt_count > 0:
            return STATUS_RETRY_SCHEDULED, "retry-scheduled"
        return STATUS_QUEUED, "queued"

    if status in {JobStatus.RUNNING, JobStatus.PARTIAL}:
        if job.ingest_status == IngestStatus.PENDING:
            return STATUS_PREPARING, "preparing"
        if job.ingest_status == IngestStatus.FAILED:
            return STATUS_FAILED, "failed"
        if not job.external_job_id:
            return STATUS_UPLOADING, "uploading"
        pipeline = (job.options or {}).get(PIPELINE_META_KEY) or {}
        stage = str(pipeline.get("stage") or "")
        if stage == "submitted":
            return STATUS_SUBMITTED, "submitted"
        return STATUS_PROCESSING, "processing"

    return str(status), "queued"


def can_show_retry(job: ProcessingJob) -> bool:
    return (
        job.status == JobStatus.FAILED
        and job.attempt_count < job.max_attempts
    )


def job_pipeline_steps(job: ProcessingJob) -> list[dict[str, str]]:
    """
    Presentation-only pipeline checklist for queue / activity cards.

    Maps existing job/ingest fields — does not invent backend state.
    """
    succeeded = job.status == JobStatus.SUCCEEDED
    failed = job.status == JobStatus.FAILED
    cancelled = job.status == JobStatus.CANCELLED

    ingest_ok = job.ingest_status in {
        IngestStatus.SUCCEEDED,
        IngestStatus.SKIPPED,
    }
    speech_ok = bool(job.external_job_id) or succeeded

    flags = [
        True,  # Audio uploaded (media exists when job exists)
        ingest_ok or speech_ok or succeeded,
        speech_ok or succeeded,
        succeeded,
        succeeded,
    ]
    labels = [
        "Audio uploaded",
        "Speech recognition",
        "Transcript created",
        "Analysis ready" if succeeded else "Analysis running",
        "Export ready",
    ]

    active_idx = None
    if not succeeded and not failed and not cancelled:
        for i, done in enumerate(flags):
            if not done:
                active_idx = i
                break

    steps: list[dict[str, str]] = []
    failed_marked = False
    for i, (done, text) in enumerate(zip(flags, labels, strict=True)):
        if done:
            state = "done"
        elif failed and not failed_marked:
            state = "failed"
            failed_marked = True
        elif active_idx == i:
            state = "active"
        else:
            state = "pending"
        steps.append({"key": f"step-{i}", "label": text, "state": state})
    return steps


def job_progress_pct(job: ProcessingJob) -> int:
    """Rough progress percent for activity bars (presentation only)."""
    steps = job_pipeline_steps(job)
    if not steps:
        return 0
    done = sum(1 for s in steps if s["state"] == "done")
    active = sum(1 for s in steps if s["state"] == "active")
    return min(100, int((done + active * 0.45) / len(steps) * 100))

