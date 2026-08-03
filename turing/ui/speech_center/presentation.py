"""Presentation helpers for Speech Center UI (no business logic)."""

from __future__ import annotations

from datetime import datetime

from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy, ngettext

from turing.domain.enums import IngestStatus, JobStatus
from turing.models import ProcessingJob
from turing.services.transcription import PIPELINE_META_KEY


# Demo labels — mapped from existing job/ingest state (translated at call time).
STATUS_QUEUED = gettext_lazy("Queued")
STATUS_PREPARING = gettext_lazy("Preparing")
STATUS_UPLOADING = gettext_lazy("Uploading")
STATUS_SUBMITTED = gettext_lazy("Submitted")
STATUS_PROCESSING = gettext_lazy("Processing")
STATUS_COMPLETED = gettext_lazy("Completed")
STATUS_RETRY_SCHEDULED = gettext_lazy("Retry Scheduled")
STATUS_FAILED = gettext_lazy("Failed")
STATUS_CANCELLED = gettext_lazy("Cancelled")

CANCELLED_BY_USER_MESSAGE = "Cancelled by user"

TERMINAL_STATUSES = frozenset(
    {
        JobStatus.SUCCEEDED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    }
)


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


def format_elapsed_seconds(total_seconds: int | float | None) -> str:
    """Human-readable elapsed duration for queue timing lines."""
    if total_seconds is None:
        return ""
    total = max(0, int(total_seconds))
    if total < 60:
        return ngettext("%(n)d second", "%(n)d seconds", total) % {"n": total}
    minutes = total // 60
    if minutes < 60:
        return ngettext("%(n)d minute", "%(n)d minutes", minutes) % {"n": minutes}
    hours = minutes // 60
    rem_minutes = minutes % 60
    if rem_minutes == 0:
        return ngettext("%(n)d hour", "%(n)d hours", hours) % {"n": hours}
    return _("%(hours)d h %(minutes)d min") % {
        "hours": hours,
        "minutes": rem_minutes,
    }


def job_timing_start(job: ProcessingJob) -> datetime | None:
    """Best timestamp for when the current processing stretch began."""
    return job.started_at or job.queued_at or job.created_at


def job_elapsed_seconds(job: ProcessingJob, *, now: datetime | None = None) -> int | None:
    """
    Seconds spent processing.

    Active jobs: from start → now.
    Terminal jobs: from start → finished_at (final duration).
    """
    start = job_timing_start(job)
    if start is None:
        return None
    if job.status in TERMINAL_STATUSES:
        end = job.finished_at or start
    else:
        end = now or timezone.now()
    if timezone.is_naive(start):
        start = timezone.make_aware(start, timezone.get_current_timezone())
    if timezone.is_naive(end):
        end = timezone.make_aware(end, timezone.get_current_timezone())
    return max(0, int((end - start).total_seconds()))


def job_timing_line(job: ProcessingJob, status_label: str, *, now: datetime | None = None) -> str:
    """Status + elapsed, e.g. ``Processing — 12 minutes``."""
    elapsed = format_elapsed_seconds(job_elapsed_seconds(job, now=now))
    if not elapsed:
        return status_label
    if job.status in TERMINAL_STATUSES:
        return _("%(status)s — %(elapsed)s total") % {
            "status": status_label,
            "elapsed": elapsed,
        }
    return _("%(status)s — %(elapsed)s") % {
        "status": status_label,
        "elapsed": elapsed,
    }


def job_display_status(job: ProcessingJob) -> tuple[str, str]:
    """
    Return ``(label, css_modifier)`` for a processing job badge.

    Pure presentation mapping over existing ``status`` / ``ingest_status`` /
    pipeline metadata — does not change job state.
    """
    status = job.status
    if status == JobStatus.FAILED:
        return str(STATUS_FAILED), "failed"
    if status == JobStatus.SUCCEEDED:
        return str(STATUS_COMPLETED), "completed"
    if status == JobStatus.CANCELLED:
        return str(STATUS_CANCELLED), "cancelled"
    if status in {JobStatus.PENDING, JobStatus.QUEUED}:
        if getattr(job, "attempt_count", 0) and job.attempt_count > 0:
            return str(STATUS_RETRY_SCHEDULED), "retry-scheduled"
        return str(STATUS_QUEUED), "queued"

    if status in {JobStatus.RUNNING, JobStatus.PARTIAL}:
        if job.ingest_status == IngestStatus.PENDING:
            return str(STATUS_PREPARING), "preparing"
        if job.ingest_status == IngestStatus.FAILED:
            return str(STATUS_FAILED), "failed"
        if not job.external_job_id:
            return str(STATUS_UPLOADING), "uploading"
        pipeline = (job.options or {}).get(PIPELINE_META_KEY) or {}
        stage = str(pipeline.get("stage") or "")
        if stage == "submitted":
            return str(STATUS_SUBMITTED), "submitted"
        return str(STATUS_PROCESSING), "processing"

    return str(status), "queued"


def can_show_retry(job: ProcessingJob) -> bool:
    return (
        job.status == JobStatus.FAILED
        and job.attempt_count < job.max_attempts
    )


def can_show_cancel(job: ProcessingJob) -> bool:
    """True for non-terminal jobs that can still be cancelled from the queue."""
    return job.status not in TERMINAL_STATUSES


def cancelled_by_user_label(job: ProcessingJob) -> str:
    """Translated cancel reason for Speech Center queue cards."""
    if job.status != JobStatus.CANCELLED:
        return ""
    raw = (job.error_message or "").strip()
    if not raw or raw == CANCELLED_BY_USER_MESSAGE:
        return _("Cancelled by user")
    return _(raw)


def job_pipeline_steps(job: ProcessingJob) -> list[dict[str, str]]:
    """
    Presentation-only pipeline checklist for queue / activity cards.

    Transcription stages only — AI analysis is manual on the transcript page.
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
        True,  # Uploading done once the job exists (media already stored)
        ingest_ok or speech_ok or succeeded,
        speech_ok or succeeded,
        succeeded,
    ]
    labels = [
        _("Uploading"),
        _("Preparing media"),
        _("Speech recognition"),
        _("Transcript ready"),
    ]

    active_idx = None
    if not succeeded and not failed and not cancelled:
        for i, done in enumerate(flags):
            if not done:
                active_idx = i
                break

    steps: list[dict[str, str]] = []
    stopped_marked = False
    for i, (done, text) in enumerate(zip(flags, labels, strict=True)):
        if done:
            state = "done"
        elif (failed or cancelled) and not stopped_marked:
            state = "failed"
            stopped_marked = True
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
