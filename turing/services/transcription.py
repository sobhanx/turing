from __future__ import annotations

import time
from datetime import datetime, timezone as dt_timezone
from typing import Any

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from turing.conf import get_turing_settings
from turing.domain.enums import JobStatus, LogLevel, RevisionSource
from turing.domain.exceptions import ProviderError, TuringError
from turing.domain.pipeline import (
    PollAction,
    PollOutcome,
    compute_poll_countdown,
    compute_submit_retry_countdown,
)
from turing.models import MediaAsset, ProcessingAttempt, ProcessingJob, Transcript
from turing.providers.registry import ProviderRegistry
from turing.providers.types import ProviderJobHandle, ProviderJobStatus, TranscriptionRequest
from turing.services.job_orchestrator import JobOrchestrator
from turing.services.transcript import TranscriptService


PIPELINE_META_KEY = "turing_pipeline"


class TranscriptionService:
    """
    STT use cases split for async execution:

    submit → poll_once (reschedulable) → fetch_and_persist

    ``process_job`` remains a synchronous run-to-completion helper for
    management commands and tests. Production uses Celery task chaining.
    """

    def __init__(
        self,
        orchestrator: JobOrchestrator | None = None,
        transcript_service: TranscriptService | None = None,
    ) -> None:
        self.orchestrator = orchestrator or JobOrchestrator()
        self.transcript_service = transcript_service or TranscriptService()

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def process_job(self, job_id: str) -> Transcript:
        """
        Synchronously run the full pipeline (CLI / tests).

        Uses short sleeps between polls — never used by Celery workers.
        """
        self.submit(job_id)
        poll_count = 0
        while True:
            outcome = self.poll_once(job_id, poll_count=poll_count)
            if outcome.action in {PollAction.READY, PollAction.ALREADY_DONE}:
                return self.fetch_and_persist(job_id)
            if outcome.action == PollAction.CANCELLED:
                raise TuringError("Job was cancelled.")
            if outcome.action == PollAction.FAILED:
                raise ProviderError(
                    outcome.error_message or "Transcription failed.",
                    code=outcome.error_code or "PROVIDER_JOB_FAILED",
                    retryable=outcome.error_code
                    in {"PROVIDER_TIMEOUT", "PROVIDER_JOB_FAILED", "PROVIDER_NETWORK"},
                )
            time.sleep(outcome.countdown or get_turing_settings().poll_interval_seconds)
            poll_count += 1

    def submit(self, job_id: str) -> str:
        """
        Submit media to the STT provider (idempotent).

        Returns: submitted | already_submitted | already_succeeded | cancelled
        """
        with transaction.atomic():
            job = (
                ProcessingJob.objects.select_for_update()
                .select_related("media")
                .get(pk=job_id)
            )
            if job.status == JobStatus.CANCELLED:
                return "cancelled"
            if job.status == JobStatus.SUCCEEDED:
                return "already_succeeded"
            existing = Transcript.objects.filter(job_id=job.id).first()
            if existing:
                attempt = self._latest_attempt(job)
                if attempt and attempt.status != JobStatus.SUCCEEDED:
                    self.orchestrator.mark_succeeded(job, attempt)
                elif not attempt:
                    job.status = JobStatus.SUCCEEDED
                    job.finished_at = timezone.now()
                    job.save(update_fields=["status", "finished_at", "updated_at"])
                return "already_succeeded"

            # Resume: provider job already created — do not re-submit
            if job.external_job_id:
                attempt = self._ensure_running_attempt(job)
                self._update_pipeline_meta(
                    attempt,
                    stage="submitted",
                    external_job_id=job.external_job_id,
                )
                self.orchestrator.log(
                    job,
                    f"Resume existing provider job {job.external_job_id} (skip re-submit).",
                    attempt=attempt,
                )
                return "already_submitted"

            attempt = self._ensure_running_attempt(job)

        # Provider I/O outside the row lock
        provider = ProviderRegistry.get(job.provider_code)
        try:
            request = self._build_request(job)
            handle = provider.submit(request)
        except ProviderError as exc:
            self.orchestrator.mark_failed(
                job,
                attempt,
                error_code=exc.code,
                error_message=exc.message,
            )
            raise
        except Exception as exc:  # noqa: BLE001
            self.orchestrator.mark_failed(
                job,
                attempt,
                error_code="INTERNAL_ERROR",
                error_message=str(exc),
            )
            raise

        with transaction.atomic():
            job = ProcessingJob.objects.select_for_update().get(pk=job_id)
            # Another worker may have submitted concurrently
            if job.external_job_id and job.external_job_id != handle.external_job_id:
                self.orchestrator.log(
                    job,
                    "Concurrent submit detected; keeping existing external_job_id.",
                    attempt=attempt,
                    level=LogLevel.WARNING,
                )
                return "already_submitted"
            job.external_job_id = handle.external_job_id
            job.status = JobStatus.RUNNING
            job.save(update_fields=["external_job_id", "status", "updated_at"])
            attempt.refresh_from_db()
            attempt.external_job_id = handle.external_job_id
            attempt.request_payload = {
                "language_code": job.language_code,
                "options": {
                    k: v for k, v in (job.options or {}).items() if not str(k).startswith("_")
                },
            }
            attempt.response_metadata = {
                **(attempt.response_metadata or {}),
                "submit": handle.metadata,
            }
            self._update_pipeline_meta(
                attempt,
                stage="submitted",
                external_job_id=handle.external_job_id,
                submitted_at=timezone.now().isoformat(),
                poll_count=0,
            )
            attempt.save(
                update_fields=[
                    "external_job_id",
                    "request_payload",
                    "response_metadata",
                    "updated_at",
                ]
            )
            self.orchestrator.log(
                job,
                f"Submitted to {job.provider_code}: {handle.external_job_id}",
                attempt=attempt,
                context={"stage": "submit"},
            )
        return "submitted"

    def poll_once(self, job_id: str, *, poll_count: int = 0) -> PollOutcome:
        """
        Single non-blocking provider status check.

        Designed so a future webhook handler can call ``apply_provider_status``
        with the same success/failure transitions.
        """
        job = self.orchestrator.get(job_id)
        if job.status == JobStatus.CANCELLED:
            return PollOutcome(action=PollAction.CANCELLED)
        if job.status == JobStatus.SUCCEEDED or Transcript.objects.filter(job=job).exists():
            return PollOutcome(action=PollAction.ALREADY_DONE)
        if not job.external_job_id:
            return PollOutcome(
                action=PollAction.FAILED,
                error_code="PIPELINE_STATE",
                error_message="Cannot poll: provider job has not been submitted.",
            )

        attempt = self._latest_attempt(job)
        settings = get_turing_settings()

        if self._is_poll_timed_out(job, attempt, settings.poll_timeout_seconds):
            message = (
                f"Timed out waiting for provider after "
                f"{settings.poll_timeout_seconds}s."
            )
            self.orchestrator.mark_failed(
                job,
                attempt,
                error_code="PROVIDER_TIMEOUT",
                error_message=message,
            )
            return PollOutcome(
                action=PollAction.FAILED,
                error_code="PROVIDER_TIMEOUT",
                error_message=message,
            )

        if settings.max_poll_attempts and poll_count >= settings.max_poll_attempts:
            message = f"Exceeded max poll attempts ({settings.max_poll_attempts})."
            self.orchestrator.mark_failed(
                job,
                attempt,
                error_code="PROVIDER_TIMEOUT",
                error_message=message,
            )
            return PollOutcome(
                action=PollAction.FAILED,
                error_code="PROVIDER_TIMEOUT",
                error_message=message,
            )

        provider = ProviderRegistry.get(job.provider_code)
        handle = ProviderJobHandle(
            external_job_id=job.external_job_id,
            provider_code=job.provider_code,
        )
        try:
            status = provider.get_status(handle)
        except ProviderError as exc:
            if exc.retryable:
                countdown = compute_poll_countdown(
                    poll_count,
                    base_seconds=settings.poll_backoff_base_seconds,
                    max_seconds=settings.poll_backoff_max_seconds,
                )
                self.orchestrator.log(
                    job,
                    f"Transient provider status error; will retry poll: {exc.message}",
                    attempt=attempt,
                    level=LogLevel.WARNING,
                    context={"error_code": exc.code, "countdown": countdown},
                )
                return PollOutcome(
                    action=PollAction.RESCHEDULE,
                    countdown=countdown,
                    provider_state="running",
                )
            self.orchestrator.mark_failed(
                job,
                attempt,
                error_code=exc.code,
                error_message=exc.message,
            )
            return PollOutcome(
                action=PollAction.FAILED,
                error_code=exc.code,
                error_message=exc.message,
            )

        return self.apply_provider_status(
            job,
            status,
            attempt=attempt,
            poll_count=poll_count,
        )

    def apply_provider_status(
        self,
        job: ProcessingJob,
        status: ProviderJobStatus,
        *,
        attempt: ProcessingAttempt | None = None,
        poll_count: int = 0,
    ) -> PollOutcome:
        """
        Shared transition logic for polling and future provider webhooks.
        """
        attempt = attempt or self._latest_attempt(job)
        settings = get_turing_settings()

        if job.status == JobStatus.CANCELLED:
            return PollOutcome(action=PollAction.CANCELLED)

        self.orchestrator.log(
            job,
            f"Provider status: {status.state}",
            attempt=attempt,
            level=LogLevel.DEBUG,
            context={
                "stage": "poll",
                "poll_count": poll_count,
                "provider_state": status.state,
            },
        )
        if attempt:
            self._update_pipeline_meta(
                attempt,
                stage="polling",
                poll_count=poll_count,
                last_poll_at=timezone.now().isoformat(),
                last_provider_state=status.state,
            )
            attempt.save(update_fields=["response_metadata", "updated_at"])

        if status.is_success:
            return PollOutcome(action=PollAction.READY, provider_state=status.state)

        if status.state == "failed":
            message = status.message or "Provider job failed."
            self.orchestrator.mark_failed(
                job,
                attempt,
                error_code="PROVIDER_JOB_FAILED",
                error_message=message,
            )
            return PollOutcome(
                action=PollAction.FAILED,
                error_code="PROVIDER_JOB_FAILED",
                error_message=message,
                provider_state=status.state,
            )

        countdown = compute_poll_countdown(
            poll_count,
            base_seconds=settings.poll_backoff_base_seconds,
            max_seconds=settings.poll_backoff_max_seconds,
        )
        return PollOutcome(
            action=PollAction.RESCHEDULE,
            countdown=countdown,
            provider_state=status.state or "running",
        )

    def fetch_and_persist(self, job_id: str) -> Transcript:
        """Fetch normalized transcript from provider and persist (idempotent)."""
        with transaction.atomic():
            job = (
                ProcessingJob.objects.select_for_update()
                .select_related("media")
                .get(pk=job_id)
            )
            if job.status == JobStatus.CANCELLED:
                raise TuringError("Job was cancelled.")
            existing = Transcript.objects.filter(job=job).select_for_update().first()
            if existing:
                attempt = self._latest_attempt(job)
                if job.status != JobStatus.SUCCEEDED and attempt:
                    self.orchestrator.mark_succeeded(job, attempt)
                return existing
            if not job.external_job_id:
                raise ProviderError(
                    "Cannot fetch: missing external_job_id.",
                    code="PIPELINE_STATE",
                    retryable=False,
                )
            attempt = self._latest_attempt(job)

        provider = ProviderRegistry.get(job.provider_code)
        handle = ProviderJobHandle(
            external_job_id=job.external_job_id,
            provider_code=job.provider_code,
        )
        try:
            if attempt:
                self._update_pipeline_meta(attempt, stage="fetching")
                attempt.save(update_fields=["response_metadata", "updated_at"])
            normalized = provider.fetch_result(handle)
            transcript = self.transcript_service.persist_from_provider(
                job=job,
                normalized=normalized,
                source=RevisionSource.PROVIDER,
            )
            if attempt:
                self._update_pipeline_meta(attempt, stage="persisted")
                attempt.save(update_fields=["response_metadata", "updated_at"])
            if attempt:
                self.orchestrator.mark_succeeded(job, attempt)
            else:
                job.status = JobStatus.SUCCEEDED
                job.finished_at = timezone.now()
                job.save(update_fields=["status", "finished_at", "updated_at"])
            return transcript
        except ProviderError as exc:
            self.orchestrator.mark_failed(
                job,
                attempt,
                error_code=exc.code,
                error_message=exc.message,
            )
            raise
        except Exception as exc:  # noqa: BLE001
            self.orchestrator.mark_failed(
                job,
                attempt,
                error_code="INTERNAL_ERROR",
                error_message=str(exc),
            )
            raise

    def should_automatic_retry(self, job: ProcessingJob, *, error_code: str) -> bool:
        retryable_codes = {
            "PROVIDER_TIMEOUT",
            "PROVIDER_JOB_FAILED",
            "PROVIDER_NETWORK",
            "PROVIDER_SERVER",
            "PROVIDER_QUOTA",
            "PROVIDER_RESPONSE",
            "INTERNAL_ERROR",
        }
        return error_code in retryable_codes and job.attempt_count < job.max_attempts

    def retry_countdown_for(self, job: ProcessingJob) -> float:
        settings = get_turing_settings()
        return compute_submit_retry_countdown(
            job.attempt_count,
            base_seconds=settings.retry_backoff_base_seconds,
            max_seconds=settings.retry_backoff_max_seconds,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _ensure_running_attempt(self, job: ProcessingJob) -> ProcessingAttempt:
        attempt = (
            ProcessingAttempt.objects.filter(job=job, status=JobStatus.RUNNING)
            .order_by("-attempt_number")
            .first()
        )
        if attempt:
            if job.status != JobStatus.RUNNING:
                job.status = JobStatus.RUNNING
                if not job.started_at:
                    job.started_at = timezone.now()
                job.save(update_fields=["status", "started_at", "updated_at"])
            return attempt
        return self.orchestrator.begin_attempt(job)

    def _latest_attempt(self, job: ProcessingJob) -> ProcessingAttempt | None:
        return job.attempts.order_by("-attempt_number").first()

    def _update_pipeline_meta(self, attempt: ProcessingAttempt, **fields: Any) -> None:
        meta = dict(attempt.response_metadata or {})
        pipeline = dict(meta.get(PIPELINE_META_KEY) or {})
        pipeline.update({k: v for k, v in fields.items() if v is not None})
        meta[PIPELINE_META_KEY] = pipeline
        attempt.response_metadata = meta

    def _is_poll_timed_out(
        self,
        job: ProcessingJob,
        attempt: ProcessingAttempt | None,
        timeout_seconds: int,
    ) -> bool:
        started = None
        if attempt:
            pipeline = (attempt.response_metadata or {}).get(PIPELINE_META_KEY) or {}
            raw = pipeline.get("submitted_at")
            if raw:
                started = parse_datetime(str(raw))
            if started is None:
                started = attempt.started_at
        if started is None:
            started = job.started_at or job.queued_at or job.created_at
        if started is None:
            return False
        if timezone.is_naive(started):
            started = timezone.make_aware(started, dt_timezone.utc)
        elapsed = (timezone.now() - started).total_seconds()
        return elapsed >= timeout_seconds

    def _build_request(self, job: ProcessingJob) -> TranscriptionRequest:
        media: MediaAsset = job.media
        options = {
            k: v for k, v in (job.options or {}).items() if not str(k).startswith("_")
        }
        diarization = bool(options.get("diarization", True))
        operating_point = str(options.get("operating_point") or "enhanced")
        extra = {
            k: v for k, v in options.items() if k not in {"diarization", "operating_point"}
        }

        request = TranscriptionRequest(
            language_code=job.language_code or "",
            diarization=diarization,
            operating_point=operating_point,
            extra_options=extra,
            filename=media.original_filename or "audio",
            content_type=media.content_type or "application/octet-stream",
        )

        if media.source_type == "url" and media.external_url:
            request.media_url = media.external_url
            return request

        from turing.services.media import MediaService

        try:
            request.media_bytes = MediaService().read_bytes(media)
            return request
        except FileNotFoundError:
            pass

        if media.file:
            media.file.open("rb")
            try:
                request.media_bytes = media.file.read()
            finally:
                media.file.close()
            return request

        if media.external_url:
            request.media_url = media.external_url
            return request

        raise ProviderError(
            "Media asset has no file or URL to transcribe.",
            code="UNSUPPORTED_MEDIA",
            retryable=False,
        )
