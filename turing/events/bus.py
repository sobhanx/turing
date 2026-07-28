from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TypeAlias

from django.db import transaction

from turing.domain.events import DomainEvent

logger = logging.getLogger(__name__)

EventHandler: TypeAlias = Callable[[DomainEvent], None]

_HANDLERS: dict[str, list[EventHandler]] = {}
_WILDCARD = "*"


class EventBus:
    """
    In-process event bus.

    Handlers run synchronously after emit. Failures are logged and swallowed so
    integrations cannot break media/STT/transcript/analysis flows.
    """

    @classmethod
    def subscribe(cls, event_name: str, handler: EventHandler) -> None:
        _HANDLERS.setdefault(event_name, []).append(handler)

    @classmethod
    def unsubscribe(cls, event_name: str, handler: EventHandler) -> None:
        handlers = _HANDLERS.get(event_name)
        if not handlers:
            return
        try:
            handlers.remove(handler)
        except ValueError:
            return
        if not handlers:
            _HANDLERS.pop(event_name, None)

    @classmethod
    def clear(cls) -> None:
        """Remove all handlers (tests)."""
        _HANDLERS.clear()

    @classmethod
    def handlers_for(cls, event_name: str) -> list[EventHandler]:
        specific = list(_HANDLERS.get(event_name, ()))
        wildcards = list(_HANDLERS.get(_WILDCARD, ()))
        return specific + wildcards

    @classmethod
    def emit(cls, event: DomainEvent) -> None:
        for handler in cls.handlers_for(event.name):
            try:
                handler(event)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Event handler failed for %s (swallowed)",
                    event.name,
                )


def emit_after_commit(event: DomainEvent) -> None:
    """
    Emit ``event`` after the current DB transaction commits.

    If no atomic block is active, emits immediately.
    """

    def _emit(ev: DomainEvent = event) -> None:
        EventBus.emit(ev)

    connection = transaction.get_connection()
    if connection.in_atomic_block:
        transaction.on_commit(_emit)
    else:
        _emit()
