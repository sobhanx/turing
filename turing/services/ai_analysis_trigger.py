"""
AI analysis trigger helpers for Speech Center (no schema changes).

Tracks in-flight / failed generation via cache so the UI can show idle,
generating, ready, or failed without altering Transcript / TranscriptAnalysis.
"""

from __future__ import annotations

from django.core.cache import cache

from turing.domain.enums import AnalysisType
from turing.models import Transcript

CACHE_KEY_PREFIX = "turing:ai_analysis:"
STATE_GENERATING = "generating"
STATE_FAILED = "failed"
# Long enough for Celery retries; cleared on success / explicit failure.
CACHE_TTL_SECONDS = 60 * 60

REQUIRED_ANALYSIS_TYPES = (
    AnalysisType.SUMMARY.value,
    AnalysisType.TOPICS.value,
    AnalysisType.ACTION_ITEMS.value,
)


def _cache_key(transcript_id: str) -> str:
    return f"{CACHE_KEY_PREFIX}{transcript_id}"


def mark_generating(transcript_id: str) -> None:
    cache.set(_cache_key(str(transcript_id)), STATE_GENERATING, CACHE_TTL_SECONDS)


def mark_failed(transcript_id: str) -> None:
    cache.set(_cache_key(str(transcript_id)), STATE_FAILED, CACHE_TTL_SECONDS)


def clear_state(transcript_id: str) -> None:
    cache.delete(_cache_key(str(transcript_id)))


def get_trigger_state(transcript_id: str) -> str | None:
    value = cache.get(_cache_key(str(transcript_id)))
    if value in {STATE_GENERATING, STATE_FAILED}:
        return value
    return None


def suite_is_complete(available: dict) -> bool:
    """True when latest-per-type map has all default insight types."""
    for key in REQUIRED_ANALYSIS_TYPES:
        row = available.get(key)
        if row is None:
            # Enum-keyed fallbacks from older callers.
            try:
                enum_key = AnalysisType(key)
            except ValueError:
                enum_key = None
            if enum_key is not None:
                row = available.get(enum_key)
        if row is None:
            return False
    return True


def has_analysis_rows(available: dict) -> bool:
    """True when at least one default insight type has a saved row."""
    for key in REQUIRED_ANALYSIS_TYPES:
        row = available.get(key)
        if row is None:
            try:
                enum_key = AnalysisType(key)
            except ValueError:
                enum_key = None
            if enum_key is not None:
                row = available.get(enum_key)
        if row is not None:
            return True
    return False


def resolve_ui_state(available: dict, transcript_id: str) -> str:
    """
    Return one of: ``ready`` | ``generating`` | ``failed`` | ``idle``.
    """
    if has_analysis_rows(available):
        if suite_is_complete(available):
            clear_state(transcript_id)
        return "ready"
    trigger = get_trigger_state(transcript_id)
    if trigger == STATE_GENERATING:
        return "generating"
    if trigger == STATE_FAILED:
        return "failed"
    return "idle"


def enqueue_transcript_analysis(transcript: Transcript) -> None:
    """Mark generating and enqueue Celery task (non-blocking)."""
    from turing.tasks.analysis import generate_transcript_analysis

    tid = str(transcript.pk)
    mark_generating(tid)
    generate_transcript_analysis.delay(tid)
