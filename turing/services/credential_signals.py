from __future__ import annotations

"""Safe operational signals for the provider credential pool (no secrets)."""

import logging
from collections import Counter
from typing import Any

logger = logging.getLogger("turing.credentials")

# Never include these (or keys containing them) in signal payloads / logs.
_FORBIDDEN_SUBSTRINGS = (
    "api_key",
    "password",
    "secret",
    "token",
    "authorization",
    "bearer",
)

# In-process counters for tests and lightweight ops scraping.
_counts: Counter[str] = Counter()


def reset_credential_signals() -> None:
    """Clear counters (tests only)."""
    _counts.clear()


def credential_signal_counts() -> dict[str, int]:
    """Snapshot of in-process event counts."""
    return dict(_counts)


def record_credential_event(event: str, **fields: Any) -> None:
    """
    Record a credential-pool operational event.

    Increments an in-process counter and emits a structured log line.
    Drops any field whose name looks like a secret.
    """
    name = (event or "").strip()
    if not name:
        return
    safe = _sanitize_fields(fields)
    _counts[name] += 1
    logger.info(
        "credential_pool event=%s%s",
        name,
        "".join(f" {key}={value!r}" for key, value in sorted(safe.items())),
    )


def _sanitize_fields(fields: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in fields.items():
        lowered = str(key).lower()
        if any(part in lowered for part in _FORBIDDEN_SUBSTRINGS):
            continue
        if isinstance(value, str) and len(value) > 256:
            value = value[:256] + "…"
        safe[str(key)] = value
    return safe
