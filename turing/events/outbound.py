from __future__ import annotations

"""
Outbound webhook delivery (Phase 4.2.2).

``OutboxDispatcher`` invokes ``enqueue_outbound_webhooks`` for every durable
event. That handler creates ``WebhookDelivery`` rows and enqueues
``deliver_webhook_delivery`` Celery tasks — it never performs HTTP inline and
never raises into outbox processing.
"""

import logging

from turing.events.outbox import OutboxDispatcher
from turing.models.outbox import OutboxEvent

logger = logging.getLogger(__name__)

_HANDLER_REGISTERED = False


def enqueue_outbound_webhooks(outbox_event: OutboxEvent) -> None:
    """Fan out HTTP deliveries for matching active subscriptions (non-blocking)."""
    from turing.services.webhook_delivery import WebhookDeliveryService

    try:
        WebhookDeliveryService().enqueue_for_outbox(outbox_event)
    except Exception:  # noqa: BLE001
        logger.exception(
            "Outbound webhook enqueue failed for outbox %s (swallowed)",
            outbox_event.id,
        )


def register_outbound_handlers() -> None:
    """Register the outbox → webhook enqueue handler (idempotent)."""
    global _HANDLER_REGISTERED
    if _HANDLER_REGISTERED:
        # Re-subscribe after OutboxDispatcher.clear() in tests.
        handlers = OutboxDispatcher.handlers_for("*")
        if enqueue_outbound_webhooks in handlers:
            return
    OutboxDispatcher.subscribe("*", enqueue_outbound_webhooks)
    _HANDLER_REGISTERED = True
