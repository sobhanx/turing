from __future__ import annotations

import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from turing.conf import get_turing_settings
from turing.domain.enums import OutboundWebhookDeliveryStatus, OutboxEventStatus
from turing.models import OutboxEvent, WebhookDelivery

logger = logging.getLogger(__name__)


class OutboxOpsService:
    """Operational queries and stuck-state recovery for outbox + webhook deliveries."""

    def pending_deliveries(self, *, organization_id: int | None = None) -> QuerySet:
        qs = WebhookDelivery.objects.filter(
            status=OutboundWebhookDeliveryStatus.PENDING
        ).select_related("subscription", "outbox_event")
        if organization_id is not None:
            qs = qs.filter(subscription__organization_id=organization_id)
        return qs.order_by("created_at")

    def failed_deliveries(self, *, organization_id: int | None = None) -> QuerySet:
        qs = WebhookDelivery.objects.filter(
            status=OutboundWebhookDeliveryStatus.FAILED
        ).select_related("subscription", "outbox_event")
        if organization_id is not None:
            qs = qs.filter(subscription__organization_id=organization_id)
        return qs.order_by("-created_at")

    def stuck_deliveries(
        self,
        *,
        older_than_seconds: float | None = None,
        organization_id: int | None = None,
    ) -> QuerySet:
        threshold = self._stuck_cutoff(older_than_seconds)
        qs = WebhookDelivery.objects.filter(
            status=OutboundWebhookDeliveryStatus.DELIVERING,
            processing_started_at__isnull=False,
            processing_started_at__lt=threshold,
        ).select_related("subscription", "outbox_event")
        if organization_id is not None:
            qs = qs.filter(subscription__organization_id=organization_id)
        return qs.order_by("processing_started_at")

    def stuck_outbox_events(
        self,
        *,
        older_than_seconds: float | None = None,
        organization_id: int | None = None,
    ) -> QuerySet:
        threshold = self._stuck_cutoff(older_than_seconds)
        qs = OutboxEvent.objects.filter(
            status=OutboxEventStatus.PROCESSING,
            processing_started_at__isnull=False,
            processing_started_at__lt=threshold,
        )
        if organization_id is not None:
            qs = qs.filter(organization_id=organization_id)
        return qs.order_by("processing_started_at")

    def recover_stuck(self, *, older_than_seconds: float | None = None) -> dict[str, int]:
        """
        Reset stuck PROCESSING/DELIVERING rows to PENDING and increment recovery_count.

        Returns counts of recovered outbox events and webhook deliveries.
        """
        outbox_n = self.recover_stuck_outbox_events(older_than_seconds=older_than_seconds)
        delivery_n = self.recover_stuck_deliveries(older_than_seconds=older_than_seconds)
        return {"outbox_events": outbox_n, "webhook_deliveries": delivery_n}

    def recover_stuck_outbox_events(
        self,
        *,
        older_than_seconds: float | None = None,
    ) -> int:
        recovered = 0
        for event in list(self.stuck_outbox_events(older_than_seconds=older_than_seconds)):
            with transaction.atomic():
                locked = (
                    OutboxEvent.objects.select_for_update()
                    .filter(
                        pk=event.pk,
                        status=OutboxEventStatus.PROCESSING,
                    )
                    .first()
                )
                if locked is None:
                    continue
                locked.status = OutboxEventStatus.PENDING
                locked.processing_started_at = None
                locked.recovery_count = locked.recovery_count + 1
                locked.last_error = (
                    f"Recovered from stuck PROCESSING (recovery #{locked.recovery_count})"
                )
                locked.save(
                    update_fields=[
                        "status",
                        "processing_started_at",
                        "recovery_count",
                        "last_error",
                        "updated_at",
                    ]
                )
                recovered += 1
                logger.warning(
                    "Recovered stuck OutboxEvent %s (recovery_count=%s)",
                    locked.id,
                    locked.recovery_count,
                )
        return recovered

    def recover_stuck_deliveries(
        self,
        *,
        older_than_seconds: float | None = None,
    ) -> int:
        from turing.tasks.webhooks import deliver_webhook_delivery

        recovered = 0
        for delivery in list(self.stuck_deliveries(older_than_seconds=older_than_seconds)):
            with transaction.atomic():
                locked = (
                    WebhookDelivery.objects.select_for_update()
                    .filter(
                        pk=delivery.pk,
                        status=OutboundWebhookDeliveryStatus.DELIVERING,
                    )
                    .first()
                )
                if locked is None:
                    continue
                locked.status = OutboundWebhookDeliveryStatus.PENDING
                locked.processing_started_at = None
                locked.recovery_count = locked.recovery_count + 1
                locked.last_error = (
                    f"Recovered from stuck DELIVERING (recovery #{locked.recovery_count})"
                )
                locked.save(
                    update_fields=[
                        "status",
                        "processing_started_at",
                        "recovery_count",
                        "last_error",
                        "updated_at",
                    ]
                )
                recovered += 1
                logger.warning(
                    "Recovered stuck WebhookDelivery %s (recovery_count=%s)",
                    locked.id,
                    locked.recovery_count,
                )
            deliver_webhook_delivery.delay(str(delivery.id))
        return recovered

    def _stuck_cutoff(self, older_than_seconds: float | None):
        settings = get_turing_settings()
        seconds = (
            float(older_than_seconds)
            if older_than_seconds is not None
            else float(settings.outbox_stuck_timeout_seconds)
        )
        return timezone.now() - timedelta(seconds=max(1.0, seconds))
