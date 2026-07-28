from __future__ import annotations

"""
Event → search index wiring (Phase 4.5.3).

Subscribes to ``transcript.created`` and ``analysis.completed``. Failures are
logged and never raised into the pipeline (EventBus also swallows handler errors).
"""

import logging

from turing.domain.events import DomainEvent, EventName
from turing.events.bus import EventBus

logger = logging.getLogger(__name__)

_HANDLER_REGISTERED = False


def _index_transcript_id(transcript_id: str, *, reason: str) -> None:
    from turing.models import Transcript
    from turing.services.search_index import SearchIndexService

    try:
        transcript = (
            Transcript.objects.select_related("organization", "media")
            .prefetch_related("segments__speaker")
            .get(pk=transcript_id)
        )
    except Transcript.DoesNotExist:
        logger.warning(
            "Search index skipped (%s): transcript %s not found",
            reason,
            transcript_id,
        )
        return

    try:
        count = SearchIndexService().index_transcript(transcript)
        logger.info(
            "Search index updated reason=%s transcript_id=%s chunks=%s",
            reason,
            transcript_id,
            count,
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "Search indexing failed reason=%s transcript_id=%s (isolated)",
            reason,
            transcript_id,
        )


def on_transcript_created(event: DomainEvent) -> None:
    transcript_id = str((event.payload or {}).get("transcript_id") or "").strip()
    if not transcript_id:
        return
    _index_transcript_id(transcript_id, reason="transcript.created")


def on_analysis_completed(event: DomainEvent) -> None:
    """
    Re-index segments when intelligence is ready (metadata refresh / future boosts).

    Does not block analysis completion.
    """
    transcript_id = str((event.payload or {}).get("transcript_id") or "").strip()
    if not transcript_id:
        return
    _index_transcript_id(transcript_id, reason="analysis.completed")


def register_search_handlers() -> None:
    """Register EventBus search index handlers (idempotent)."""
    global _HANDLER_REGISTERED
    if _HANDLER_REGISTERED:
        for name, handler in (
            (EventName.TRANSCRIPT_CREATED, on_transcript_created),
            (EventName.ANALYSIS_COMPLETED, on_analysis_completed),
        ):
            if handler not in EventBus.handlers_for(name):
                EventBus.subscribe(name, handler)
        return
    EventBus.subscribe(EventName.TRANSCRIPT_CREATED, on_transcript_created)
    EventBus.subscribe(EventName.ANALYSIS_COMPLETED, on_analysis_completed)
    _HANDLER_REGISTERED = True
