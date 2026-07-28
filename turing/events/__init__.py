from __future__ import annotations

"""
Internal event foundation (Phase 4.1.3+) with durable outbox (Phase 4.2.1).

Events are notifications for host integrations. They do not replace Celery
and must never break the speech processing pipeline.
"""

from turing.domain.events import DomainEvent, EventName
from turing.events.bus import EventBus, emit_after_commit
from turing.events.outbox import OutboxDispatcher, persist_domain_event

__all__ = [
    "DomainEvent",
    "EventBus",
    "EventName",
    "OutboxDispatcher",
    "emit_after_commit",
    "persist_domain_event",
]
