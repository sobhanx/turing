from __future__ import annotations

from django.conf import settings
from django.db import models

from turing.domain.enums import Capability, IngestStatus, JobStatus, LogLevel
from turing.models.media import UUIDModel


class ProcessingJob(UUIDModel):
    """
    Async unit of speech intelligence work.

    Capability-aware so future AI features (summarization, sentiment, …)
    share the same job pipeline as STT.
    """

    media = models.ForeignKey(
        "turing.MediaAsset",
        on_delete=models.CASCADE,
        related_name="jobs",
    )
    capability = models.CharField(
        max_length=32,
        choices=Capability.choices,
        default=Capability.STT,
        db_index=True,
    )
    provider_code = models.CharField(max_length=64, db_index=True)
    status = models.CharField(
        max_length=16,
        choices=JobStatus.choices,
        default=JobStatus.PENDING,
        db_index=True,
    )
    priority = models.PositiveSmallIntegerField(default=100, db_index=True)
    language_code = models.CharField(
        max_length=16,
        blank=True,
        default="",
        help_text=(
            "STT language (e.g. fa, en). Empty uses Platform / provider default language; "
            "job creation fails if no default is configured."
        ),
    )
    options = models.JSONField(
        default=dict,
        blank=True,
        help_text="Diarization, punctuation, operating point, etc.",
    )
    external_job_id = models.CharField(max_length=255, blank=True, default="", db_index=True)
    idempotency_key = models.CharField(max_length=128, blank=True, default="")
    attempt_count = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=3)
    error_code = models.CharField(max_length=64, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    queued_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="turing_jobs",
    )
    organization = models.ForeignKey(
        "turing.Organization",
        on_delete=models.PROTECT,
        related_name="jobs",
        help_text="Copied from media at job creation. Required.",
    )
    tenant_key = models.CharField(max_length=64, blank=True, default="", db_index=True)
    expected_duration_ms = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Detected audio duration used for poll timeout scaling.",
    )
    ingest_artifact = models.ForeignKey(
        "turing.MediaProcessingArtifact",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="jobs",
        help_text="Normalized artifact used for STT submit, when available.",
    )
    ingest_status = models.CharField(
        max_length=16,
        choices=IngestStatus.choices,
        default=IngestStatus.PENDING,
        db_index=True,
    )
    ingest_error = models.TextField(
        blank=True,
        default="",
        help_text="Ingestion failure reason when ingest_status is failed.",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "priority", "created_at"]),
            models.Index(fields=["media", "capability"]),
            models.Index(fields=["tenant_key", "-created_at"]),
            models.Index(fields=["organization", "-created_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="turing_job_idempotency_key_uniq",
            ),
        ]
        verbose_name = "Processing job"
        verbose_name_plural = "Processing jobs"

    def __str__(self) -> str:
        return f"Job({self.id} {self.capability}/{self.provider_code} {self.status})"


class ProcessingAttempt(UUIDModel):
    """One provider execution attempt for a job (including retries)."""

    job = models.ForeignKey(
        ProcessingJob,
        on_delete=models.CASCADE,
        related_name="attempts",
    )
    attempt_number = models.PositiveSmallIntegerField()
    provider_code = models.CharField(max_length=64)
    external_job_id = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(
        max_length=16,
        choices=JobStatus.choices,
        default=JobStatus.RUNNING,
    )
    request_payload = models.JSONField(default=dict, blank=True)
    response_metadata = models.JSONField(default=dict, blank=True)
    error_code = models.CharField(max_length=64, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["attempt_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["job", "attempt_number"],
                name="turing_attempt_job_number_uniq",
            ),
        ]
        verbose_name = "Processing attempt"
        verbose_name_plural = "Processing attempts"

    def __str__(self) -> str:
        return f"Attempt({self.job_id} #{self.attempt_number})"


class ProcessingLog(UUIDModel):
    """Append-only operational log for jobs."""

    job = models.ForeignKey(
        ProcessingJob,
        on_delete=models.CASCADE,
        related_name="logs",
    )
    attempt = models.ForeignKey(
        ProcessingAttempt,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="logs",
    )
    level = models.CharField(max_length=16, choices=LogLevel.choices, default=LogLevel.INFO)
    message = models.TextField()
    context = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["job", "created_at"]),
            models.Index(fields=["level", "created_at"]),
        ]
        verbose_name = "Processing log"
        verbose_name_plural = "Processing logs"

    def __str__(self) -> str:
        return f"[{self.level}] {self.message[:80]}"
