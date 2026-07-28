from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name="turing.tasks.events.dispatch_outbox_events",
    acks_late=True,
    max_retries=0,
)
def dispatch_outbox_events(self, limit: int = 100) -> dict:
    """
    Recover stuck work, claim pending ``OutboxEvent`` rows, run handlers.

    Outbound webhook fan-out enqueues ``deliver_webhook_delivery`` (HTTP not inline).
    Scheduled via Celery Beat when ``TURING_OUTBOX_DISPATCH_ENABLED`` is true.
    """
    from turing.events.outbox import dispatch_pending
    from turing.events.outbound import register_outbound_handlers

    register_outbound_handlers()
    counts = dispatch_pending(limit=limit)
    logger.info(
        "Outbox dispatch claimed=%s delivered=%s failed=%s "
        "recovered_outbox=%s recovered_deliveries=%s",
        counts["claimed"],
        counts["delivered"],
        counts["failed"],
        counts.get("recovered_outbox", 0),
        counts.get("recovered_deliveries", 0),
    )
    return counts


@shared_task(
    bind=True,
    name="turing.tasks.events.recover_stuck_outbox_work",
    acks_late=True,
    max_retries=0,
)
def recover_stuck_outbox_work(self) -> dict:
    """Reset stuck PROCESSING/DELIVERING rows older than the configured timeout."""
    from turing.services.outbox_ops import OutboxOpsService

    counts = OutboxOpsService().recover_stuck()
    logger.info(
        "Outbox recovery outbox_events=%s webhook_deliveries=%s",
        counts["outbox_events"],
        counts["webhook_deliveries"],
    )
    return counts
