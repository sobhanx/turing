from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name="turing.tasks.webhooks.process_provider_webhook_event",
    acks_late=True,
    max_retries=8,
    autoretry_for=(),
)
def process_provider_webhook_event(self, notification_data: dict) -> str:
    """Process a normalized provider webhook notification asynchronously."""
    from turing.domain.exceptions import ProviderError
    from turing.services.transcription import TranscriptionService
    from turing.webhooks.types import ProviderNotification

    notification = ProviderNotification.from_dict(notification_data)
    service = TranscriptionService()
    try:
        return service.ingest_provider_notification(notification)
    except ProviderError as exc:
        if getattr(exc, "retryable", False) and self.request.retries < self.max_retries:
            countdown = min(300, 2 ** int(self.request.retries))
            logger.warning(
                "Retryable webhook processing failure for %s job %s (retry in %ss)",
                notification.provider_code,
                notification.external_job_id,
                countdown,
            )
            raise self.retry(exc=exc, countdown=countdown)
        logger.exception(
            "Webhook processing failed for %s job %s",
            notification.provider_code,
            notification.external_job_id,
        )
        raise
    except Exception as exc:
        if self.request.retries < self.max_retries:
            countdown = min(300, 2 ** int(self.request.retries))
            logger.warning(
                "Transient webhook processing failure for %s job %s (retry in %ss)",
                notification.provider_code,
                notification.external_job_id,
                countdown,
            )
            raise self.retry(exc=exc, countdown=countdown)
        logger.exception(
            "Webhook processing failed for %s job %s",
            notification.provider_code,
            notification.external_job_id,
        )
        raise


@shared_task(
    bind=True,
    name="turing.tasks.webhooks.deliver_webhook_delivery",
    acks_late=True,
    max_retries=20,
)
def deliver_webhook_delivery(self, delivery_id: str) -> str:
    """
    Deliver one outbound ``WebhookDelivery`` with exponential backoff retries.

    Failures are stored on the delivery row and never fail the parent outbox event.
    """
    from turing.conf import get_turing_settings
    from turing.models import WebhookDelivery
    from turing.services.webhook_delivery import WebhookDeliveryService

    service = WebhookDeliveryService()
    outcome = service.attempt_delivery(delivery_id)
    if outcome == "retry":
        try:
            delivery = WebhookDelivery.objects.only("attempts").get(pk=delivery_id)
            countdown = service.retry_countdown(delivery.attempts)
        except WebhookDelivery.DoesNotExist:
            return "failed"
        settings = get_turing_settings()
        max_retries = int(settings.outbound_webhook_max_retries)
        raise self.retry(countdown=countdown, max_retries=max_retries)
    return outcome
