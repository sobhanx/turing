from __future__ import annotations

"""Celery Beat schedule helpers (outbox + connector sync)."""

from typing import Any

from django.conf import settings


def outbox_dispatch_enabled() -> bool:
    return bool(getattr(settings, "TURING_OUTBOX_DISPATCH_ENABLED", True))


def outbox_dispatch_interval_seconds() -> float:
    return float(getattr(settings, "TURING_OUTBOX_DISPATCH_INTERVAL_SECONDS", 30))


def connector_sync_enabled() -> bool:
    return bool(getattr(settings, "TURING_CONNECTOR_SYNC_ENABLED", True))


def connector_sync_interval_seconds() -> float:
    return float(getattr(settings, "TURING_CONNECTOR_SYNC_INTERVAL_SECONDS", 3600))


def build_celery_beat_schedule() -> dict[str, dict[str, Any]]:
    """
    Build ``CELERY_BEAT_SCHEDULE`` for outbox and connector sync.

    Each feature is independently toggleable:
    - ``TURING_OUTBOX_DISPATCH_ENABLED``
    - ``TURING_CONNECTOR_SYNC_ENABLED``

    When disabled, that feature's Beat entries are omitted (safe no-op).
    """
    schedule: dict[str, dict[str, Any]] = {}

    if outbox_dispatch_enabled():
        interval = max(1.0, outbox_dispatch_interval_seconds())
        schedule["turing-dispatch-outbox-events"] = {
            "task": "turing.tasks.events.dispatch_outbox_events",
            "schedule": interval,
            "options": {"queue": "turing.default"},
        }
        schedule["turing-recover-stuck-outbox"] = {
            "task": "turing.tasks.events.recover_stuck_outbox_work",
            "schedule": interval,
            "options": {"queue": "turing.default"},
        }

    if connector_sync_enabled():
        interval = max(1.0, connector_sync_interval_seconds())
        schedule["turing-schedule-connector-syncs"] = {
            "task": "turing.tasks.connectors.schedule_connector_syncs",
            "schedule": interval,
            "options": {"queue": "turing.default"},
        }

    return schedule
