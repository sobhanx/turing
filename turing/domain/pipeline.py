from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum


class PollAction(str, Enum):
    """Outcome of a single non-blocking provider status check."""

    RESCHEDULE = "reschedule"  # still running — schedule another poll
    READY = "ready"  # provider succeeded — fetch + persist
    FAILED = "failed"  # provider failed or timed out
    CANCELLED = "cancelled"
    ALREADY_DONE = "already_done"


@dataclass(frozen=True)
class PollOutcome:
    action: PollAction
    countdown: float = 0.0
    error_code: str = ""
    error_message: str = ""
    provider_state: str = ""


def compute_poll_countdown(
    poll_count: int,
    *,
    base_seconds: float,
    max_seconds: float,
    jitter_ratio: float = 0.2,
) -> float:
    """
    Exponential backoff for poll reschedules.

    delay = min(base * 2^poll_count, max) with optional jitter.
    """
    delay = min(float(base_seconds) * (2 ** max(poll_count, 0)), float(max_seconds))
    if jitter_ratio > 0:
        jitter = delay * jitter_ratio * random.random()
        delay = delay * (1.0 - jitter_ratio / 2.0) + jitter
    return max(0.5, round(delay, 3))


def compute_submit_retry_countdown(
    attempt_number: int,
    *,
    base_seconds: float = 5.0,
    max_seconds: float = 300.0,
) -> float:
    """Backoff before starting a new submit attempt after a retryable failure."""
    delay = min(float(base_seconds) * (2 ** max(attempt_number - 1, 0)), float(max_seconds))
    return max(1.0, round(delay, 3))


def compute_poll_timeout_seconds(
    *,
    base_timeout_seconds: int,
    expected_duration_ms: int | None,
    multiplier: float = 2.0,
) -> int:
    """
    Duration-aware poll timeout.

    timeout = max(base_timeout, duration_seconds * multiplier)
    """
    base = max(int(base_timeout_seconds), 1)
    if not expected_duration_ms or expected_duration_ms <= 0:
        return base
    scaled = int((expected_duration_ms / 1000.0) * float(multiplier))
    return max(base, scaled)
