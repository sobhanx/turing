from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TypeAlias

from django.db import connection, transaction
from django.utils import timezone

from turing.domain.enums import OutboxEventStatus
from turing.domain.events import DomainEvent
from turing.models.outbox import OutboxEvent

logger = logging.getLogger(__name__)

OutboxHandler: TypeAlias = Callable[[OutboxEvent], None]

_DISPATCH_HANDLERS: dict[str, list[OutboxHandler]] = {}
_WILDCARD = "*"

DEFAULT_BATCH_SIZE = 100


def persist_domain_event(event: DomainEvent) -> OutboxEvent | None:
    """
    Persist a durable outbox row for ``event``.

    Failures are logged and swallowed so integrations never break the pipeline.
    Returns the created row, or ``None`` if persistence was skipped/failed.
    """
    try:
        organization_id = event.payload.get("organization_id")
        if organization_id is None:
            logger.warning(
                "Skipping outbox persist for %s: missing organization_id",
                event.name,
            )
            return None
        return OutboxEvent.objects.create(
            organization_id=organization_id,
            event_name=event.name,
            payload=dict(event.payload),
            status=OutboxEventStatus.PENDING,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Failed to persist outbox event for %s (swallowed)", event.name)
        return None


class OutboxDispatcher:
    """
    Registry for durable outbox handlers (async path).

    Distinct from the in-process ``EventBus``. Handlers here run from the
    ``dispatch_outbox_events`` Celery task. HTTP webhook delivery will register
    here later; Phase 4.2.1 only provides the dispatch foundation.
    """

    @classmethod
    def subscribe(cls, event_name: str, handler: OutboxHandler) -> None:
        _DISPATCH_HANDLERS.setdefault(event_name, []).append(handler)

    @classmethod
    def unsubscribe(cls, event_name: str, handler: OutboxHandler) -> None:
        handlers = _DISPATCH_HANDLERS.get(event_name)
        if not handlers:
            return
        try:
            handlers.remove(handler)
        except ValueError:
            return
        if not handlers:
            _DISPATCH_HANDLERS.pop(event_name, None)

    @classmethod
    def clear(cls) -> None:
        """Remove all handlers (tests)."""
        _DISPATCH_HANDLERS.clear()

    @classmethod
    def handlers_for(cls, event_name: str) -> list[OutboxHandler]:
        specific = list(_DISPATCH_HANDLERS.get(event_name, ()))
        wildcards = list(_DISPATCH_HANDLERS.get(_WILDCARD, ()))
        return specific + wildcards

    @classmethod
    def dispatch(cls, outbox_event: OutboxEvent) -> None:
        """
        Run all registered handlers for ``outbox_event``.

        Individual handler failures are logged and swallowed so one consumer
        (e.g. outbound webhooks) cannot fail the durable outbox row for others.
        """
        for handler in cls.handlers_for(outbox_event.event_name):
            try:
                handler(outbox_event)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Outbox handler failed for %s event %s (swallowed)",
                    outbox_event.event_name,
                    outbox_event.id,
                )


def _pending_queryset():
    qs = OutboxEvent.objects.filter(status=OutboxEventStatus.PENDING).order_by(
        "created_at"
    )
    if connection.vendor == "postgresql":
        return qs.select_for_update(skip_locked=True)
    return qs.select_for_update()


def claim_pending_events(*, limit: int = DEFAULT_BATCH_SIZE) -> list[OutboxEvent]:
    """Atomically claim a batch of pending outbox rows (PENDING → PROCESSING)."""
    now = timezone.now()
    with transaction.atomic():
        events = list(_pending_queryset()[:limit])
        for event in events:
            event.status = OutboxEventStatus.PROCESSING
            event.attempts = event.attempts + 1
            event.last_error = ""
            event.processing_started_at = now
            event.save(
                update_fields=[
                    "status",
                    "attempts",
                    "last_error",
                    "processing_started_at",
                    "updated_at",
                ]
            )
        return events


def finalize_delivered(event: OutboxEvent) -> OutboxEvent:
    event.status = OutboxEventStatus.DELIVERED
    event.delivered_at = timezone.now()
    event.last_error = ""
    event.processing_started_at = None
    event.save(
        update_fields=[
            "status",
            "delivered_at",
            "last_error",
            "processing_started_at",
            "updated_at",
        ]
    )
    return event


def finalize_failed(event: OutboxEvent, error: BaseException | str) -> OutboxEvent:
    message = str(error)
    event.status = OutboxEventStatus.FAILED
    event.last_error = message[:4000]
    event.processing_started_at = None
    event.save(
        update_fields=["status", "last_error", "processing_started_at", "updated_at"]
    )
    return event


def process_outbox_event(event: OutboxEvent) -> str:
    """Dispatch handlers for a claimed event and update status. Returns final status."""
    # Handlers isolate their own failures; unexpected dispatch errors still fail the row.
    try:
        OutboxDispatcher.dispatch(event)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Outbox dispatch failed for %s", event.id)
        finalize_failed(event, exc)
        return OutboxEventStatus.FAILED
    finalize_delivered(event)
    return OutboxEventStatus.DELIVERED


def dispatch_pending(*, limit: int = DEFAULT_BATCH_SIZE) -> dict[str, int]:
    """Recover stuck rows, then claim and process a batch of pending outbox events."""
    from turing.services.outbox_ops import OutboxOpsService

    recovered = OutboxOpsService().recover_stuck()
    claimed = claim_pending_events(limit=limit)
    counts = {
        "claimed": len(claimed),
        "delivered": 0,
        "failed": 0,
        "recovered_outbox": recovered["outbox_events"],
        "recovered_deliveries": recovered["webhook_deliveries"],
    }
    for event in claimed:
        status = process_outbox_event(event)
        if status == OutboxEventStatus.DELIVERED:
            counts["delivered"] += 1
        else:
            counts["failed"] += 1
    return counts
