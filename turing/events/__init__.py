from __future__ import annotations

"""
Internal event foundation (Phase 4.1.3).

Events are notifications for future host integrations. They do not replace Celery
and must never break the speech processing pipeline.
"""

from turing.domain.events import DomainEvent, EventName
from turing.events.bus import EventBus, emit_after_commit

__all__ = [
    "DomainEvent",
    "EventBus",
    "EventName",
    "emit_after_commit",
]
