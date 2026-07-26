from __future__ import annotations

from turing.domain.enums import JobStatus, TranscriptStatus, TuringRole
from turing.domain.exceptions import JobStateError, PermissionDeniedError


TERMINAL_JOB_STATUSES = frozenset(
    {
        JobStatus.SUCCEEDED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    }
)

RETRYABLE_JOB_STATUSES = frozenset({JobStatus.FAILED, JobStatus.PARTIAL})

EDITABLE_TRANSCRIPT_STATUSES = frozenset(
    {
        TranscriptStatus.DRAFT,
        TranscriptStatus.IN_REVIEW,
    }
)

# Allowed from → to transitions for ProcessingJob.status
ALLOWED_JOB_TRANSITIONS: dict[str, frozenset[str]] = {
    JobStatus.PENDING: frozenset(
        {JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.CANCELLED, JobStatus.FAILED}
    ),
    JobStatus.QUEUED: frozenset(
        {JobStatus.RUNNING, JobStatus.PENDING, JobStatus.CANCELLED, JobStatus.FAILED}
    ),
    JobStatus.RUNNING: frozenset(
        {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.RUNNING,  # idempotent / resume
        }
    ),
    JobStatus.FAILED: frozenset(
        {JobStatus.QUEUED, JobStatus.PENDING, JobStatus.RUNNING, JobStatus.CANCELLED}
    ),
    JobStatus.PARTIAL: frozenset(
        {JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.FAILED, JobStatus.CANCELLED}
    ),
    JobStatus.SUCCEEDED: frozenset({JobStatus.SUCCEEDED}),  # idempotent only
    JobStatus.CANCELLED: frozenset({JobStatus.CANCELLED}),
}

ROLE_CAPABILITIES: dict[str, frozenset[str]] = {
    TuringRole.ADMIN: frozenset(
        {
            "manage_config",
            "manage_roles",
            "manage_jobs",
            "retry_jobs",
            "cancel_jobs",
            "upload_media",
            "edit_transcript",
            "review_transcript",
            "approve_transcript",
            "view_transcript",
        }
    ),
    TuringRole.REVIEWER: frozenset(
        {
            "manage_jobs",
            "upload_media",
            "edit_transcript",
            "review_transcript",
            "approve_transcript",
            "view_transcript",
        }
    ),
    TuringRole.EDITOR: frozenset(
        {
            "upload_media",
            "edit_transcript",
            "view_transcript",
        }
    ),
    TuringRole.USER: frozenset(
        {
            "upload_media",
            "view_transcript",
        }
    ),
    TuringRole.VIEWER: frozenset(
        {
            "view_transcript",
        }
    ),
}


def assert_job_transition(current: str, new: str) -> None:
    """Validate a ProcessingJob status transition."""
    if current == new:
        return
    allowed = ALLOWED_JOB_TRANSITIONS.get(current, frozenset())
    if new not in allowed:
        raise JobStateError(
            f"Invalid job transition '{current}' → '{new}'."
        )


def assert_job_can_enqueue(status: str) -> None:
    if status not in {JobStatus.PENDING, JobStatus.FAILED, JobStatus.QUEUED}:
        raise JobStateError(f"Cannot enqueue job in status '{status}'.")


def assert_job_can_retry(status: str, attempt_count: int, max_attempts: int) -> None:
    if status not in RETRYABLE_JOB_STATUSES:
        raise JobStateError(f"Cannot retry job in status '{status}'.")
    if attempt_count >= max_attempts:
        raise JobStateError("Maximum retry attempts exceeded.")


def assert_job_can_cancel(status: str) -> None:
    if status in TERMINAL_JOB_STATUSES:
        raise JobStateError(f"Cannot cancel job in terminal status '{status}'.")


def assert_job_can_succeed(status: str) -> None:
    if status == JobStatus.CANCELLED:
        raise JobStateError("Cannot mark a cancelled job as succeeded.")
    if status == JobStatus.SUCCEEDED:
        return
    assert_job_transition(status, JobStatus.SUCCEEDED)


def assert_job_can_fail(status: str) -> None:
    if status in {JobStatus.SUCCEEDED, JobStatus.CANCELLED}:
        raise JobStateError(f"Cannot mark job as failed from status '{status}'.")
    if status == JobStatus.FAILED:
        return
    assert_job_transition(status, JobStatus.FAILED)


def assert_transcript_editable(status: str) -> None:
    allowed = {
        TranscriptStatus.DRAFT,
        TranscriptStatus.IN_REVIEW,
    }
    if status not in allowed:
        raise JobStateError(f"Transcript in status '{status}' is not editable.")


def assert_can_submit_for_review(status: str) -> None:
    if status == TranscriptStatus.APPROVED:
        raise JobStateError("Approved transcripts cannot be submitted for review.")
    if status == TranscriptStatus.ARCHIVED:
        raise JobStateError("Archived transcripts cannot be submitted for review.")


def assert_can_approve(status: str) -> None:
    if status not in {TranscriptStatus.DRAFT, TranscriptStatus.IN_REVIEW}:
        raise JobStateError(f"Cannot approve transcript in status '{status}'.")


def role_has_capability(role: str, capability: str) -> bool:
    return capability in ROLE_CAPABILITIES.get(role, frozenset())


def assert_capability(role: str, capability: str) -> None:
    if not role_has_capability(role, capability):
        raise PermissionDeniedError(
            f"Role '{role}' does not have capability '{capability}'."
        )
