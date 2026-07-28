from __future__ import annotations

from django.db import models

from turing.domain.enums import OutboxEventStatus
from turing.models.media import UUIDModel


class OutboxEvent(UUIDModel):
    """
    Durable copy of a domain event for reliable async dispatch.

    Written after commit via the EventBus. Does not store transcript text or
    analysis content — payload is IDs + organization + external references only.
    """

    organization = models.ForeignKey(
        "turing.Organization",
        on_delete=models.PROTECT,
        related_name="outbox_events",
        db_index=True,
        help_text="Owning organization (data boundary). Required.",
    )
    event_name = models.CharField(max_length=128, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=16,
        choices=OutboxEventStatus.choices,
        default=OutboxEventStatus.PENDING,
        db_index=True,
    )
    delivered_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True, default="")
    processing_started_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Set when status becomes PROCESSING; used for stuck recovery.",
    )
    recovery_count = models.PositiveIntegerField(
        default=0,
        help_text="How many times this row was reset from stuck PROCESSING.",
    )

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Outbox event"
        verbose_name_plural = "Outbox events"
        indexes = [
            models.Index(
                fields=["status", "created_at"],
                name="turing_outbox_pending_scan",
            ),
            models.Index(
                fields=["status", "processing_started_at"],
                name="turing_outbox_stuck_scan",
            ),
        ]

    def __str__(self) -> str:
        return f"OutboxEvent({self.event_name} {self.status} {self.id})"
