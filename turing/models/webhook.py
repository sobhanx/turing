from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import models

from turing.domain.enums import OutboundWebhookDeliveryStatus
from turing.domain.events import SUPPORTED_OUTBOUND_EVENT_NAMES
from turing.models.media import TimeStampedModel, UUIDModel
from turing.security.fields import EncryptedCharField


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


_http_https_url = URLValidator(schemes=["http", "https"])


class WebhookSubscription(UUIDModel):
    """
    Org-scoped host subscription for outbound Turing domain events.

    Secrets are encrypted at rest and must never appear in API serializers or
    Admin readonly displays (write-only password field only).
    """

    organization = models.ForeignKey(
        "turing.Organization",
        on_delete=models.PROTECT,
        related_name="webhook_subscriptions",
        db_index=True,
    )
    name = models.CharField(max_length=128)
    url = models.URLField(max_length=2048)
    secret = EncryptedCharField(
        max_length=512,
        blank=True,
        default="",
        help_text="HMAC signing secret (encrypted at rest). Never expose in API/Admin.",
    )
    subscribed_events = models.JSONField(
        default=list,
        blank=True,
        help_text='Event names to receive, e.g. ["transcript.created"]. Use ["*"] for all.',
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Webhook subscription"
        verbose_name_plural = "Webhook subscriptions"
        indexes = [
            models.Index(
                fields=["organization", "is_active"],
                name="turing_whsub_org_active",
            ),
        ]

    def __str__(self) -> str:
        state = "active" if self.is_active else "inactive"
        return f"WebhookSubscription({self.name} [{state}])"

    def clean(self) -> None:
        super().clean()
        url = (self.url or "").strip()
        if not url:
            raise ValidationError({"url": "URL is required."})
        try:
            _http_https_url(url)
        except ValidationError as exc:
            raise ValidationError({"url": "Enter a valid http(s) URL."}) from exc
        events = self.subscribed_events
        if events is None:
            self.subscribed_events = []
            events = []
        if not isinstance(events, list):
            raise ValidationError(
                {"subscribed_events": "Must be a JSON list of event name strings."}
            )
        cleaned: list[str] = []
        for item in events:
            if not isinstance(item, str) or not item.strip():
                raise ValidationError(
                    {"subscribed_events": "Each entry must be a non-empty string."}
                )
            cleaned.append(item.strip())
        if not cleaned:
            raise ValidationError(
                {"subscribed_events": "Select at least one event (or '*')."}
            )
        unknown = sorted(
            {
                name
                for name in cleaned
                if name != "*" and name not in SUPPORTED_OUTBOUND_EVENT_NAMES
            }
        )
        if unknown:
            raise ValidationError(
                {
                    "subscribed_events": (
                        "Unknown event name(s): "
                        + ", ".join(unknown)
                        + ". Supported: "
                        + ", ".join(sorted(SUPPORTED_OUTBOUND_EVENT_NAMES))
                        + ", *."
                    )
                }
            )
        self.subscribed_events = cleaned

    def accepts_event(self, event_name: str) -> bool:
        if not self.is_active:
            return False
        events = self.subscribed_events or []
        if "*" in events:
            return True
        return event_name in events


class WebhookDelivery(UUIDModel):
    """
    One outbound HTTP delivery attempt track for a subscription + outbox event.

    HTTP work runs in ``deliver_webhook_delivery``; failures stay on this row and
    must not fail the parent outbox event.
    """

    subscription = models.ForeignKey(
        WebhookSubscription,
        on_delete=models.CASCADE,
        related_name="deliveries",
    )
    outbox_event = models.ForeignKey(
        "turing.OutboxEvent",
        on_delete=models.CASCADE,
        related_name="outbound_webhook_deliveries",
    )
    status = models.CharField(
        max_length=16,
        choices=OutboundWebhookDeliveryStatus.choices,
        default=OutboundWebhookDeliveryStatus.PENDING,
        db_index=True,
    )
    attempts = models.PositiveIntegerField(default=0)
    response_status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    response_body_preview = models.TextField(blank=True, default="")
    last_error = models.TextField(blank=True, default="")
    delivered_at = models.DateTimeField(null=True, blank=True)
    processing_started_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Set when status becomes DELIVERING; used for stuck recovery.",
    )
    recovery_count = models.PositiveIntegerField(
        default=0,
        help_text="How many times this row was reset from stuck DELIVERING.",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Webhook delivery"
        verbose_name_plural = "Webhook deliveries"
        constraints = [
            models.UniqueConstraint(
                fields=["subscription", "outbox_event"],
                name="turing_whdel_sub_outbox_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "created_at"], name="turing_whdel_status"),
            models.Index(
                fields=["subscription", "-created_at"],
                name="turing_whdel_sub",
            ),
            models.Index(
                fields=["status", "processing_started_at"],
                name="turing_whdel_stuck_scan",
            ),
        ]

    def __str__(self) -> str:
        return f"WebhookDelivery({self.status} sub={self.subscription_id} evt={self.outbox_event_id})"
