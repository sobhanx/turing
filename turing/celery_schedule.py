from __future__ import annotations

"""Celery Beat schedule helpers for Turing outbox reliability (Phase 4.2.3)."""

from typing import Any

from django.conf import settings


def outbox_dispatch_enabled() -> bool:
    return bool(getattr(settings, "TURING_OUTBOX_DISPATCH_ENABLED", True))


def outbox_dispatch_interval_seconds() -> float:
    return float(getattr(settings, "TURING_OUTBOX_DISPATCH_INTERVAL_SECONDS", 30))


def build_celery_beat_schedule() -> dict[str, dict[str, Any]]:
    """
    Build ``CELERY_BEAT_SCHEDULE`` entries for outbox dispatch + recovery.

    Disabled safely when ``TURING_OUTBOX_DISPATCH_ENABLED`` is false (empty schedule
    for these tasks). Hosts may still call ``dispatch_outbox_events`` manually.
    """
    schedule: dict[str, dict[str, Any]] = {}
    if not outbox_dispatch_enabled():
        return schedule

    interval = max(1.0, outbox_dispatch_interval_seconds())
    schedule["turing-dispatch-outbox-events"] = {
        "task": "turing.tasks.events.dispatch_outbox_events",
        "schedule": interval,
        "options": {"queue": "turing.default"},
    }
    # Recovery also runs inside dispatch; keep a dedicated entry so stuck rows
    # are cleared even if the dispatch batch limit is saturated.
    schedule["turing-recover-stuck-outbox"] = {
        "task": "turing.tasks.events.recover_stuck_outbox_work",
        "schedule": interval,
        "options": {"queue": "turing.default"},
    }
    return schedule
