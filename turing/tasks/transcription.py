from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name="turing.tasks.transcription.process_transcription_job",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=3,
    acks_late=True,
)
def process_transcription_job(self, job_id: str) -> str:
    """Background STT pipeline for a ProcessingJob."""
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
        transcript = service.process_job(str(job.id))
        return str(transcript.id)
    except ProviderError as exc:
        logger.exception("Provider error for job %s: %s", job_id, exc)
        # Domain already marked failed; retry only if retryable and attempts remain
        job.refresh_from_db()
        if exc.retryable and job.attempt_count < job.max_attempts:
            raise self.retry(exc=exc)
        return f"failed:{exc.code}"
    except Exception as exc:
        logger.exception("Unexpected error for job %s", job_id)
        raise self.retry(exc=exc)
