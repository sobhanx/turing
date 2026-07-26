from __future__ import annotations

import time
from typing import Any

from django.db import transaction

from turing.conf import get_turing_settings
from turing.domain.enums import JobStatus, LogLevel, RevisionSource
from turing.domain.exceptions import ProviderError, TuringError
from turing.models import MediaAsset, ProcessingAttempt, ProcessingJob, Transcript
from turing.providers.registry import ProviderRegistry
from turing.providers.types import TranscriptionRequest
from turing.services.job_orchestrator import JobOrchestrator
from turing.services.transcript import TranscriptService


class TranscriptionService:
    """
    End-to-end STT use case:

    resolve provider → submit → poll → normalize → persist transcript.
    """

    def __init__(
        self,
        orchestrator: JobOrchestrator | None = None,
        transcript_service: TranscriptService | None = None,
    ) -> None:
        self.orchestrator = orchestrator or JobOrchestrator()
        self.transcript_service = transcript_service or TranscriptService()

    def process_job(self, job_id: str) -> Transcript:
        job = self.orchestrator.get(job_id)
        if job.status == JobStatus.CANCELLED:
            raise TuringError("Job was cancelled.")
        if job.status == JobStatus.SUCCEEDED and hasattr(job, "transcript"):
            return job.transcript

        attempt = self.orchestrator.begin_attempt(job)
        provider = ProviderRegistry.get(job.provider_code)

        try:
            request = self._build_request(job)
            handle = provider.submit(request)
            job.external_job_id = handle.external_job_id
            job.save(update_fields=["external_job_id", "updated_at"])
            attempt.external_job_id = handle.external_job_id
            attempt.request_payload = {
                "language_code": job.language_code,
                "options": job.options,
            }
            attempt.response_metadata = handle.metadata
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
            )

            self._poll_until_done(job, attempt, provider, handle)
            normalized = provider.fetch_result(handle)
            transcript = self.transcript_service.persist_from_provider(
                job=job,
                normalized=normalized,
                source=RevisionSource.PROVIDER,
            )
            self.orchestrator.mark_succeeded(job, attempt)
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

    def _poll_until_done(
        self,
        job: ProcessingJob,
        attempt: ProcessingAttempt,
        provider: Any,
        handle: Any,
    ) -> None:
        settings = get_turing_settings()
        deadline = time.monotonic() + settings.poll_timeout_seconds
        interval = settings.poll_interval_seconds

        while time.monotonic() < deadline:
            job.refresh_from_db(fields=["status"])
            if job.status == JobStatus.CANCELLED:
                provider.cancel(handle)
                raise TuringError("Job cancelled during provider polling.")

            status = provider.get_status(handle)
            self.orchestrator.log(
                job,
                f"Provider status: {status.state}",
                attempt=attempt,
                level=LogLevel.DEBUG,
                context={"raw_status": status.raw},
            )
            if status.is_success:
                return
            if status.state == "failed":
                raise ProviderError(
                    status.message or "Provider job failed.",
                    code="PROVIDER_JOB_FAILED",
                    retryable=True,
                    provider_code=job.provider_code,
                )
            time.sleep(interval)

        raise ProviderError(
            "Timed out waiting for provider job.",
            code="PROVIDER_TIMEOUT",
            retryable=True,
            provider_code=job.provider_code,
        )

    def _build_request(self, job: ProcessingJob) -> TranscriptionRequest:
        media: MediaAsset = job.media
        options = job.options or {}
        diarization = bool(options.get("diarization", True))
        operating_point = str(options.get("operating_point") or "enhanced")
        extra = {k: v for k, v in options.items() if k not in {"diarization", "operating_point"}}

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
