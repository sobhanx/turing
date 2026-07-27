from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name="turing.tasks.webhooks.process_provider_webhook_event",
    acks_late=True,
    max_retries=0,
)
def process_provider_webhook_event(self, notification_data: dict) -> str:
    """Process a normalized provider webhook notification asynchronously."""
    from turing.services.transcription import TranscriptionService
    from turing.webhooks.types import ProviderNotification

    notification = ProviderNotification.from_dict(notification_data)
    service = TranscriptionService()
    try:
        return service.ingest_provider_notification(notification)
    except Exception:
        logger.exception(
            "Webhook processing failed for %s job %s",
            notification.provider_code,
            notification.external_job_id,
        )
        raise
