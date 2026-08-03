from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


def _schedule_poll(job_id: str, *, poll_count: int, countdown: float) -> None:
    from turing.models import ProcessingJob
    from turing.services.job_orchestrator import JobOrchestrator

    async_result = poll_transcription_job.apply_async(
        args=[job_id],
        kwargs={"poll_count": poll_count},
        countdown=max(0.0, float(countdown)),
    )
    job = ProcessingJob.objects.filter(pk=job_id).first()
    if job is not None:
        JobOrchestrator().remember_celery_task_id(
            job, getattr(async_result, "id", None)
        )


def _maybe_auto_retry(job_id: str, *, error_code: str) -> str:
    from turing.domain.enums import JobStatus
    from turing.models import ProcessingJob
    from turing.services.job_orchestrator import JobOrchestrator
    from turing.services.transcription import TranscriptionService

    job = ProcessingJob.objects.get(pk=job_id)
    if job.status == JobStatus.CANCELLED:
        return "cancelled"
    service = TranscriptionService()
    if not service.should_automatic_retry(job, error_code=error_code):
        return f"failed:{error_code}"
    countdown = service.retry_countdown_for(job)
    JobOrchestrator().enqueue(job, countdown=countdown, clear_external_job=True)
    return f"retry_scheduled:{error_code}:{countdown}"


@shared_task(
    bind=True,
    name="turing.tasks.transcription.process_transcription_job",
    acks_late=True,
    max_retries=0,
)
def process_transcription_job(self, job_id: str) -> str:
    """
    Backward-compatible entrypoint: start the async prepare → submit → poll → persist pipeline.
    """
    from turing.tasks.ingestion import prepare_media_for_transcription

    return prepare_media_for_transcription(job_id)


@shared_task(
    bind=True,
    name="turing.tasks.transcription.submit_transcription_job",
    acks_late=True,
    max_retries=0,
)
def submit_transcription_job(self, job_id: str) -> str:
    """Step 1: submit media to the STT provider (idempotent)."""
    from turing.domain.enums import JobStatus
    from turing.domain.exceptions import ProviderError
    from turing.models import ProcessingJob
    from turing.services.transcription import TranscriptionService

    try:
        job = ProcessingJob.objects.get(pk=job_id)
    except ProcessingJob.DoesNotExist:
        logger.error("ProcessingJob %s not found", job_id)
        return "missing"

    if job.status == JobStatus.CANCELLED:
        return "cancelled"
    if job.status == JobStatus.SUCCEEDED:
        return "already_succeeded"

    service = TranscriptionService()
    try:
        result = service.submit(str(job.id))
    except ProviderError as exc:
        logger.exception("Submit failed for job %s: %s", job_id, exc)
        return _maybe_auto_retry(job_id, error_code=exc.code)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected submit error for job %s", job_id)
        job.refresh_from_db()
        if job.status != JobStatus.FAILED:
            from turing.services.job_orchestrator import JobOrchestrator

            attempt = job.attempts.order_by("-attempt_number").first()
            JobOrchestrator().mark_failed(
                job,
                attempt,
                error_code="INTERNAL_ERROR",
                error_message=str(exc),
            )
        return _maybe_auto_retry(job_id, error_code="INTERNAL_ERROR")

    if result in {"submitted", "already_submitted"}:
        _schedule_poll(str(job.id), poll_count=0, countdown=0)
        return result
    if result == "submit_in_progress":
        # Another worker holds the submit claim — retry shortly.
        async_result = submit_transcription_job.apply_async(
            args=[str(job.id)], countdown=2.0
        )
        from turing.services.job_orchestrator import JobOrchestrator

        JobOrchestrator().remember_celery_task_id(
            job, getattr(async_result, "id", None)
        )
        return result
    return result


@shared_task(
    bind=True,
    name="turing.tasks.transcription.poll_transcription_job",
    acks_late=True,
    max_retries=0,
)
def poll_transcription_job(self, job_id: str, poll_count: int = 0) -> str:
    """
    Step 2: one non-blocking provider status check.

    If still running, reschedules itself with exponential backoff (no sleep).
    Ready to be replaced/augmented by provider webhooks via
    TranscriptionService.apply_provider_status.
    """
    from turing.domain.enums import JobStatus
    from turing.domain.pipeline import PollAction
    from turing.models import ProcessingJob
    from turing.services.job_orchestrator import JobOrchestrator
    from turing.services.transcription import TranscriptionService

    service = TranscriptionService()
    try:
        outcome = service.poll_once(job_id, poll_count=poll_count)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Poll error for job %s", job_id)
        job = ProcessingJob.objects.filter(pk=job_id).first()
        if job and job.status not in {
            JobStatus.FAILED,
            JobStatus.SUCCEEDED,
            JobStatus.CANCELLED,
        }:
            attempt = job.attempts.order_by("-attempt_number").first()
            JobOrchestrator().mark_failed(
                job,
                attempt,
                error_code="INTERNAL_ERROR",
                error_message=str(exc),
            )
        return _maybe_auto_retry(job_id, error_code="INTERNAL_ERROR")

    if outcome.action == PollAction.RESCHEDULE:
        _schedule_poll(
            job_id,
            poll_count=poll_count + 1,
            countdown=outcome.countdown,
        )
        return f"rescheduled:{outcome.countdown}"

    if outcome.action in {PollAction.READY, PollAction.ALREADY_DONE}:
        fetch_and_persist_transcription.delay(job_id)
        return outcome.action.value

    if outcome.action == PollAction.CANCELLED:
        return "cancelled"

    return _maybe_auto_retry(
        job_id,
        error_code=outcome.error_code or "PROVIDER_JOB_FAILED",
    )


@shared_task(
    bind=True,
    name="turing.tasks.transcription.fetch_and_persist_transcription",
    acks_late=True,
    max_retries=0,
)
def fetch_and_persist_transcription(self, job_id: str) -> str:
    """Steps 3–4: fetch provider transcript and persist segments/speakers/revision."""
    from turing.domain.exceptions import ProviderError, TuringError
    from turing.services.transcription import TranscriptionService

    service = TranscriptionService()
    try:
        transcript, created = service._fetch_and_persist_with_created(job_id)
        if created:
            from turing.models import Organization
            from turing.services.ai_analysis_trigger import enqueue_transcript_analysis

            auto = False
            if transcript.organization_id:
                auto = bool(
                    Organization.objects.filter(pk=transcript.organization_id)
                    .values_list("auto_generate_ai_analysis", flat=True)
                    .first()
                )
            if auto:
                enqueue_transcript_analysis(transcript)
        return str(transcript.id)
    except TuringError as exc:
        logger.warning("Fetch/persist aborted for job %s: %s", job_id, exc)
        return f"aborted:{exc}"
    except ProviderError as exc:
        logger.exception("Fetch/persist provider error for job %s", job_id)
        return _maybe_auto_retry(job_id, error_code=exc.code)
    except Exception:  # noqa: BLE001
        logger.exception("Fetch/persist unexpected error for job %s", job_id)
        return _maybe_auto_retry(job_id, error_code="INTERNAL_ERROR")
