from __future__ import annotations

from django.db import models

from turing.models.media import TimeStampedModel


class WebhookDeliveryOutcome(models.TextChoices):
    PROCESSED = "processed", "Processed"
    DUPLICATE = "duplicate", "Duplicate"
    IGNORED = "ignored", "Ignored"
    UNKNOWN_JOB = "unknown_job", "Unknown job"


class ProviderWebhookDelivery(TimeStampedModel):
    """Audit + dedupe record for provider webhook notifications (Phase 3.1)."""

    provider_code = models.CharField(max_length=64, db_index=True)
    external_job_id = models.CharField(max_length=255, db_index=True)
    status_param = models.CharField(max_length=64, blank=True, default="")
    dedupe_key = models.CharField(max_length=64)
    payload_hash = models.CharField(max_length=64, blank=True, default="")
    processing_job = models.ForeignKey(
        "turing.ProcessingJob",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="webhook_deliveries",
    )
    outcome = models.CharField(
        max_length=32,
        choices=WebhookDeliveryOutcome.choices,
        db_index=True,
    )
    raw_metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "Provider webhook delivery"
        verbose_name_plural = "Provider webhook deliveries"
        constraints = [
            models.UniqueConstraint(
                fields=["provider_code", "dedupe_key"],
                name="turing_webhook_delivery_dedupe_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["provider_code", "external_job_id", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"Webhook({self.provider_code}:{self.external_job_id} {self.outcome})"
