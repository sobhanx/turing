from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


def _fail_ingestion(job, *, error_code: str, error_message: str) -> str:
    from turing.domain.enums import IngestStatus
    from turing.services.job_orchestrator import JobOrchestrator

    job.ingest_status = IngestStatus.FAILED
    job.ingest_error = error_message
    job.save(update_fields=["ingest_status", "ingest_error", "updated_at"])

    attempt = job.attempts.order_by("-attempt_number").first()
    JobOrchestrator().mark_failed(
        job,
        attempt,
        error_code=error_code,
        error_message=error_message,
    )
    return f"failed:{error_code}"


@shared_task(
    bind=True,
    name="turing.tasks.ingestion.prepare_media_for_transcription",
    acks_late=True,
    max_retries=0,
)
def prepare_media_for_transcription(self, job_id: str) -> str:
    """
    Inspect and normalize media before STT submit.

    Fail-closed: corrupt, unreadable, probe, or normalization failures mark the
    job failed and do not schedule STT submit.
    """
    from turing.domain.enums import IngestStatus, JobStatus
    from turing.domain.exceptions import IngestionError, ValidationError
    from turing.models import ProcessingJob
    from turing.services.media_ingestion import MediaIngestionService
    from turing.tasks.transcription import submit_transcription_job

    try:
        job = ProcessingJob.objects.select_related("media").get(pk=job_id)
    except ProcessingJob.DoesNotExist:
        logger.error("ProcessingJob %s not found for ingestion", job_id)
        return "missing"

    if job.status == JobStatus.CANCELLED:
        return "cancelled"
    if job.status == JobStatus.SUCCEEDED:
        return "already_succeeded"

    service = MediaIngestionService()
    try:
        result = service.prepare_for_job(job)
    except IngestionError as exc:
        logger.warning("Ingestion failed for job %s: %s", job_id, exc)
        return _fail_ingestion(
            job,
            error_code=exc.code.upper() if exc.code else "INGESTION_ERROR",
            error_message=exc.message,
        )
    except ValidationError as exc:
        logger.warning("Ingestion validation failed for job %s: %s", job_id, exc)
        return _fail_ingestion(
            job,
            error_code="VALIDATION_ERROR",
            error_message=exc.message,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected ingestion failure for job %s", job_id)
        return _fail_ingestion(
            job,
            error_code="INGESTION_ERROR",
            error_message=str(exc),
        )

    if result.status == IngestStatus.SUCCEEDED:
        if result.artifact and result.artifact.status == "ready":
            logger.info("Ingestion produced normalized artifact for job %s", job_id)
        elif result.used_original:
            logger.info("Ingestion passed with original media for job %s", job_id)
    elif result.status == IngestStatus.SKIPPED:
        logger.info("Ingestion skipped for job %s", job_id)

    submit_transcription_job.delay(job_id)
    return "prepared"
