from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser
from django.utils import timezone

from turing.conf import get_turing_settings
from turing.domain.enums import Capability, JobStatus, LogLevel
from turing.domain.exceptions import JobStateError, NotFoundError, ValidationError
from turing.domain.policies import assert_job_can_cancel, assert_job_can_retry
from turing.models import MediaAsset, ProcessingAttempt, ProcessingJob, ProcessingLog
from turing.providers.registry import ProviderRegistry


class JobOrchestrator:
    """Create, enqueue, retry, and cancel processing jobs."""

    def create_transcription_job(
        self,
        *,
        media: MediaAsset,
        provider_code: str | None = None,
        language_code: str = "",
        options: dict | None = None,
        created_by: AbstractBaseUser | None = None,
        idempotency_key: str = "",
        priority: int = 100,
        auto_enqueue: bool | None = None,
    ) -> ProcessingJob:
        settings = get_turing_settings()
        code = provider_code or settings.default_provider
        if code not in ProviderRegistry.codes():
            raise ValidationError(f"Provider '{code}' is not registered.")

        if idempotency_key:
            existing = ProcessingJob.objects.filter(idempotency_key=idempotency_key).first()
            if existing:
                return existing

        opts = dict(options or {})
        if "diarization" not in opts:
            opts["diarization"] = settings.enable_diarization_default
        language = self._resolve_language_code(
            language_code=language_code,
            provider_code=code,
        )

        job = ProcessingJob.objects.create(
            media=media,
            capability=Capability.STT,
            provider_code=code,
            status=JobStatus.PENDING,
            priority=priority,
            language_code=language,
            options=opts,
            idempotency_key=idempotency_key or "",
            max_attempts=settings.default_max_attempts,
            created_by=created_by,
            tenant_key=media.tenant_key,
        )
        self.log(job, "Job created.", level=LogLevel.INFO, context={"provider": code})

        should_enqueue = settings.auto_enqueue if auto_enqueue is None else auto_enqueue
        if should_enqueue:
            self.enqueue(job)
        return job

    def _resolve_language_code(self, *, language_code: str, provider_code: str) -> str:
        """
        Resolve STT language for a new job.

        Order:
        1. Explicit language_code argument
        2. PlatformConfiguration / settings default_language
        3. SpeechProviderConfig.default_language for the selected provider

        Raises ValidationError if none are set — never silently omit language.
        """
        explicit = (language_code or "").strip()
        if explicit:
            return explicit

        settings = get_turing_settings()
        platform_default = (settings.default_language or "").strip()
        if platform_default:
            return platform_default

        from turing.models.configuration import SpeechProviderConfig

        provider = SpeechProviderConfig.objects.filter(
            code=provider_code,
            is_active=True,
        ).first()
        if provider:
            provider_default = (provider.default_language or "").strip()
            if provider_default:
                return provider_default

        raise ValidationError(
            "language_code is required. Pass language_code when creating the job, "
            "or set Platform configuration → Default language "
            "(e.g. fa for Persian), or Speech provider configs → Default language."
        )

    def enqueue(
        self,
        job: ProcessingJob,
        *,
        countdown: float = 0.0,
        clear_external_job: bool | None = None,
    ) -> ProcessingJob:
        if job.status not in {
            JobStatus.PENDING,
            JobStatus.FAILED,
            JobStatus.QUEUED,
        }:
            raise JobStateError(f"Cannot enqueue job in status '{job.status}'.")

        # Retries after failure must re-submit unless caller resumes an existing provider job
        if clear_external_job is None:
            clear_external_job = job.status == JobStatus.FAILED

        job.status = JobStatus.QUEUED
        job.queued_at = timezone.now()
        job.finished_at = None
        if clear_external_job:
            job.external_job_id = ""
        # Keep last error visible until a successful run clears it in mark_succeeded
        job.save(
            update_fields=[
                "status",
                "queued_at",
                "finished_at",
                "external_job_id",
                "updated_at",
            ]
        )
        self.log(
            job,
            "Job queued for async transcription pipeline.",
            context={"countdown": countdown, "clear_external_job": clear_external_job},
        )

        from turing.tasks.transcription import submit_transcription_job

        try:
            async_result = submit_transcription_job.apply_async(
                args=[str(job.id)],
                countdown=max(0.0, float(countdown)),
            )
            self.log(
                job,
                "Celery submit task scheduled.",
                context={"task_id": getattr(async_result, "id", None), "countdown": countdown},
            )
        except Exception as exc:  # noqa: BLE001
            job.error_code = "ENQUEUE_FAILED"
            job.error_message = (
                f"Failed to schedule Celery task (is Redis/broker running?): {exc}"
            )
            job.status = JobStatus.PENDING
            job.save(
                update_fields=[
                    "status",
                    "error_code",
                    "error_message",
                    "updated_at",
                ]
            )
            self.log(
                job,
                job.error_message,
                level=LogLevel.ERROR,
                context={"error_code": "ENQUEUE_FAILED"},
            )
        return job

    def retry(self, job: ProcessingJob) -> ProcessingJob:
        assert_job_can_retry(job.status, job.attempt_count, job.max_attempts)
        return self.enqueue(job, clear_external_job=True)

    def cancel(self, job: ProcessingJob) -> ProcessingJob:
        assert_job_can_cancel(job.status)
        job.status = JobStatus.CANCELLED
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "finished_at", "updated_at"])
        self.log(job, "Job cancelled.")
        return job

    def get(self, job_id) -> ProcessingJob:
        try:
            return ProcessingJob.objects.select_related("media").get(pk=job_id)
        except ProcessingJob.DoesNotExist as exc:
            raise NotFoundError(f"Job '{job_id}' not found.") from exc

    def begin_attempt(self, job: ProcessingJob) -> ProcessingAttempt:
        job.attempt_count += 1
        job.status = JobStatus.RUNNING
        job.started_at = timezone.now()
        job.error_code = ""
        job.error_message = ""
        job.save(
            update_fields=[
                "attempt_count",
                "status",
                "started_at",
                "error_code",
                "error_message",
                "updated_at",
            ]
        )
        attempt = ProcessingAttempt.objects.create(
            job=job,
            attempt_number=job.attempt_count,
            provider_code=job.provider_code,
            status=JobStatus.RUNNING,
            started_at=timezone.now(),
        )
        self.log(job, f"Attempt #{attempt.attempt_number} started.", attempt=attempt)
        return attempt

    def mark_succeeded(self, job: ProcessingJob, attempt: ProcessingAttempt) -> None:
        now = timezone.now()
        attempt.status = JobStatus.SUCCEEDED
        attempt.finished_at = now
        attempt.save(update_fields=["status", "finished_at", "updated_at"])
        job.status = JobStatus.SUCCEEDED
        job.finished_at = now
        job.error_code = ""
        job.error_message = ""
        job.save(
            update_fields=[
                "status",
                "finished_at",
                "error_code",
                "error_message",
                "updated_at",
            ]
        )
        self.log(job, "Job succeeded.", attempt=attempt, level=LogLevel.INFO)

    def mark_failed(
        self,
        job: ProcessingJob,
        attempt: ProcessingAttempt | None,
        *,
        error_code: str,
        error_message: str,
    ) -> None:
        now = timezone.now()
        if attempt:
            attempt.status = JobStatus.FAILED
            attempt.error_code = error_code
            attempt.error_message = error_message
            attempt.finished_at = now
            attempt.save(
                update_fields=[
                    "status",
                    "error_code",
                    "error_message",
                    "finished_at",
                    "updated_at",
                ]
            )
        job.status = JobStatus.FAILED
        job.error_code = error_code
        job.error_message = error_message
        job.finished_at = now
        job.save(
            update_fields=[
                "status",
                "error_code",
                "error_message",
                "finished_at",
                "updated_at",
            ]
        )
        self.log(
            job,
            f"Job failed: {error_message}",
            attempt=attempt,
            level=LogLevel.ERROR,
            context={"error_code": error_code},
        )

    def log(
        self,
        job: ProcessingJob,
        message: str,
        *,
        level: str = LogLevel.INFO,
        attempt: ProcessingAttempt | None = None,
        context: dict | None = None,
    ) -> ProcessingLog:
        return ProcessingLog.objects.create(
            job=job,
            attempt=attempt,
            level=level,
            message=message,
            context=context or {},
        )
