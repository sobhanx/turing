from __future__ import annotations

import logging

from django.contrib.auth.models import AbstractBaseUser
from django.db import IntegrityError, transaction
from django.utils import timezone

from turing.conf import get_turing_settings
from turing.domain.enums import Capability, JobStatus, LogLevel
from turing.domain.events import job_completed
from turing.domain.exceptions import JobStateError, NotFoundError, ValidationError
from turing.domain.policies import (
    assert_job_can_cancel,
    assert_job_can_enqueue,
    assert_job_can_fail,
    assert_job_can_retry,
    assert_job_can_succeed,
    assert_job_transition,
)
from turing.events.bus import emit_after_commit
from turing.events.payloads import snapshot_external_references
from turing.models import MediaAsset, ProcessingAttempt, ProcessingJob, ProcessingLog
from turing.providers.registry import ProviderRegistry
from turing.providers.types import ProviderJobHandle

logger = logging.getLogger(__name__)

# Soft-tracked Celery task ids for best-effort revoke on cancel (presentation/ops).
CELERY_TASK_IDS_KEY = "_celery_task_ids"


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

        if created_by is not None:
            from turing.auth.tenancy import assert_organization_access

            assert_organization_access(
                created_by,
                media.organization,
                capability="manage_jobs",
            )

        if idempotency_key:
            existing = ProcessingJob.objects.filter(
                organization=media.organization,
                idempotency_key=idempotency_key,
            ).first()
            if existing:
                return existing

        opts = dict(options or {})
        if "diarization" not in opts:
            opts["diarization"] = settings.enable_diarization_default
        language = self._resolve_language_code(
            language_code=language_code,
            provider_code=code,
        )

        try:
            with transaction.atomic():
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
                    organization=media.organization,
                    tenant_key=media.tenant_key,
                )
        except IntegrityError:
            if idempotency_key:
                existing = ProcessingJob.objects.filter(
                    organization=media.organization,
                    idempotency_key=idempotency_key,
                ).first()
                if existing:
                    return existing
            raise

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
        assert_job_can_enqueue(job.status)

        # Retries after failure must re-submit unless caller resumes an existing provider job
        if clear_external_job is None:
            clear_external_job = job.status == JobStatus.FAILED

        assert_job_transition(job.status, JobStatus.QUEUED)
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

        from turing.tasks.ingestion import prepare_media_for_transcription

        try:
            async_result = prepare_media_for_transcription.apply_async(
                args=[str(job.id)],
                countdown=max(0.0, float(countdown)),
            )
            self.remember_celery_task_id(job, getattr(async_result, "id", None))
            self.log(
                job,
                "Celery prepare task scheduled.",
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
        """
        Cancel locally and best-effort cancel the provider job.

        Also best-effort revokes tracked Celery task ids so pending countdown
        retries do not continue. Provider cancel failures are logged but do not
        undo local cancellation.
        """
        with transaction.atomic():
            locked = (
                ProcessingJob.objects.select_for_update()
                .select_related("media")
                .get(pk=job.pk)
            )
            assert_job_can_cancel(locked.status)
            assert_job_transition(locked.status, JobStatus.CANCELLED)
            external_job_id = locked.external_job_id
            provider_code = locked.provider_code
            attempt = None
            if external_job_id:
                attempt = (
                    ProcessingAttempt.objects.filter(
                        job_id=locked.pk,
                        external_job_id=external_job_id,
                    )
                    .select_related("provider_credential")
                    .order_by("-attempt_number")
                    .first()
                )
            locked.status = JobStatus.CANCELLED
            locked.finished_at = timezone.now()
            locked.error_code = "CANCELLED_BY_USER"
            locked.error_message = "Cancelled by user"
            locked.save(
                update_fields=[
                    "status",
                    "finished_at",
                    "error_code",
                    "error_message",
                    "updated_at",
                ]
            )
            self.log(locked, "Job cancelled by user.")
            job = locked

        self.revoke_celery_tasks(job)

        if external_job_id:
            self.cancel_provider_job(
                job,
                external_job_id=external_job_id,
                provider_code=provider_code,
                attempt=attempt,
            )
        return job

    def remember_celery_task_id(
        self, job: ProcessingJob, task_id: str | None
    ) -> None:
        """Persist a scheduled Celery task id for later revoke-on-cancel."""
        tid = (task_id or "").strip()
        if not tid:
            return
        opts = dict(job.options or {})
        ids = [str(x) for x in (opts.get(CELERY_TASK_IDS_KEY) or []) if x]
        if tid in ids:
            return
        ids.append(tid)
        opts[CELERY_TASK_IDS_KEY] = ids[-20:]
        job.options = opts
        job.save(update_fields=["options", "updated_at"])

    def revoke_celery_tasks(self, job: ProcessingJob) -> int:
        """
        Best-effort revoke of tracked Celery tasks for this job.

        Uses ``terminate=False`` so an in-flight worker is not killed mid-request;
        in-flight tasks still exit early after the next CANCELLED status check.
        """
        ids = [str(x) for x in ((job.options or {}).get(CELERY_TASK_IDS_KEY) or []) if x]
        if not ids:
            return 0
        revoked = 0
        try:
            from celery import current_app
        except Exception:  # noqa: BLE001
            return 0
        for tid in ids:
            try:
                current_app.control.revoke(tid, terminate=False)
                revoked += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to revoke Celery task %s for job %s: %s", tid, job.id, exc
                )
        if revoked:
            self.log(
                job,
                f"Revoked {revoked} pending Celery task(s).",
                context={"task_ids": ids},
            )
        return revoked

    def cancel_provider_job(
        self,
        job: ProcessingJob,
        *,
        external_job_id: str | None = None,
        provider_code: str | None = None,
        attempt: ProcessingAttempt | None = None,
    ) -> bool:
        """
        Best-effort provider cancel. Returns True if cancel was attempted successfully.

        Prefer the sticky credential on ``attempt`` (or the Attempt owning
        ``external_job_id``). Never pick a different pool credential for cancel.
        """
        eid = (external_job_id or job.external_job_id or "").strip()
        if not eid:
            return False
        code = provider_code or job.provider_code
        if attempt is None:
            attempt = (
                ProcessingAttempt.objects.filter(job_id=job.pk, external_job_id=eid)
                .select_related("provider_credential")
                .order_by("-attempt_number")
                .first()
            )
        try:
            from turing.services.transcription import TranscriptionService

            provider = TranscriptionService()._provider_for_attempt(
                job, attempt, provider_code=code
            )
            provider.cancel(
                ProviderJobHandle(external_job_id=eid, provider_code=code)
            )
            self.log(
                job,
                f"Requested provider cancel for {eid}.",
                attempt=attempt,
                context={"external_job_id": eid},
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Provider cancel failed for job %s (%s): %s", job.id, eid, exc
            )
            self.log(
                job,
                f"Provider cancel failed (ignored): {exc}",
                attempt=attempt,
                level=LogLevel.WARNING,
                context={"external_job_id": eid, "error": str(exc)},
            )
            return False

    def get(self, job_id) -> ProcessingJob:
        try:
            return ProcessingJob.objects.select_related("media").get(pk=job_id)
        except ProcessingJob.DoesNotExist as exc:
            raise NotFoundError(f"Job '{job_id}' not found.") from exc

    def begin_attempt(self, job: ProcessingJob) -> ProcessingAttempt:
        """
        Start a new provider execution Attempt.

        Invariant: the provider credential is selected once here and remains
        sticky for submit, poll, fetch, and cancel. Credential rotation happens
        only by creating a new Attempt (e.g. after failure + retry).
        """
        assert_job_transition(job.status, JobStatus.RUNNING)
        from turing.services.credential_manager import CredentialManager

        with transaction.atomic():
            # Empty pool → provider_credential=NULL; sticky I/O uses legacy
            # SpeechProviderConfig.api_key / env via adapter fallback.
            credential = CredentialManager.acquire(job.provider_code)

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
                provider_credential=credential,
                status=JobStatus.RUNNING,
                started_at=timezone.now(),
            )
            self.log(
                job, f"Attempt #{attempt.attempt_number} started.", attempt=attempt
            )
            if credential is not None:
                self.log(
                    job,
                    "Provider credential selected for attempt",
                    attempt=attempt,
                    context={
                        "credential_id": str(credential.id),
                        "credential_name": credential.name,
                    },
                )
            return attempt

    def mark_succeeded(
        self,
        job: ProcessingJob,
        attempt: ProcessingAttempt | None = None,
    ) -> bool:
        """
        Mark job succeeded. Returns False if skipped (e.g. already cancelled).

        Emits ``job.completed`` exactly once when transitioning into SUCCEEDED.
        ``attempt`` may be None for edge paths that succeed without an attempt row.
        """
        job.refresh_from_db()
        if job.status == JobStatus.CANCELLED:
            self.log(
                job,
                "Skip mark_succeeded; job already cancelled.",
                attempt=attempt,
                level=LogLevel.WARNING,
            )
            return False
        try:
            assert_job_can_succeed(job.status)
        except JobStateError as exc:
            self.log(
                job,
                f"Skip mark_succeeded: {exc.message}",
                attempt=attempt,
                level=LogLevel.WARNING,
            )
            return False
        if job.status == JobStatus.SUCCEEDED:
            return True

        now = timezone.now()
        if attempt is not None:
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
        self._emit_job_completed(job)
        return True

    def _emit_job_completed(self, job: ProcessingJob) -> None:
        from turing.models import Transcript

        transcript_id = (
            Transcript.objects.filter(job_id=job.id)
            .values_list("id", flat=True)
            .first()
        )
        media_id = job.media_id
        emit_after_commit(
            job_completed(
                job_id=str(job.id),
                organization_id=job.organization_id,
                media_id=str(media_id) if media_id else None,
                transcript_id=str(transcript_id) if transcript_id else None,
                external_references=snapshot_external_references(
                    organization_id=job.organization_id,
                    media_id=media_id,
                ),
            )
        )

    def mark_failed(
        self,
        job: ProcessingJob,
        attempt: ProcessingAttempt | None,
        *,
        error_code: str,
        error_message: str,
    ) -> bool:
        job.refresh_from_db()
        if job.status in {JobStatus.SUCCEEDED, JobStatus.CANCELLED}:
            self.log(
                job,
                f"Skip mark_failed; job already {job.status}.",
                attempt=attempt,
                level=LogLevel.WARNING,
                context={"error_code": error_code},
            )
            return False
        try:
            assert_job_can_fail(job.status)
        except JobStateError as exc:
            self.log(
                job,
                f"Skip mark_failed: {exc.message}",
                attempt=attempt,
                level=LogLevel.WARNING,
            )
            return False

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
        return True

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
