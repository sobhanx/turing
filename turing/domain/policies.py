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


def assert_transcript_editable(status: str) -> None:
    allowed = {
        TranscriptStatus.DRAFT,
        TranscriptStatus.IN_REVIEW,
    }
    if status not in allowed:
        raise JobStateError(f"Transcript in status '{status}' is not editable.")


def role_has_capability(role: str, capability: str) -> bool:
    return capability in ROLE_CAPABILITIES.get(role, frozenset())


def assert_capability(role: str, capability: str) -> None:
    if not role_has_capability(role, capability):
        raise PermissionDeniedError(
            f"Role '{role}' does not have capability '{capability}'."
        )
