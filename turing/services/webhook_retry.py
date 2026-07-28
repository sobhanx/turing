from __future__ import annotations

"""HTTP retry policy for outbound webhook delivery (Phase 4.2.3)."""

# Client / auth errors — permanent for this delivery (do not retry).
NON_RETRYABLE_HTTP_STATUS_CODES = frozenset({400, 401, 403, 404})

# Explicit retryable statuses (plus all other 5xx).
RETRYABLE_HTTP_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


def is_retryable_http_status(status_code: int | None) -> bool:
    """
    Return whether an HTTP status should be retried.

    Retry: timeouts/network (caller), 429, 5xx.
    Do not retry: 400, 401, 403, 404 (and other 4xx except 429).
    """
    if status_code is None:
        return True  # connection / timeout — no status
    if status_code in NON_RETRYABLE_HTTP_STATUS_CODES:
        return False
    if status_code == 429:
        return True
    if 500 <= status_code <= 599:
        return True
    if status_code in RETRYABLE_HTTP_STATUS_CODES:
        return True
    # Other 2xx handled as success; other 3xx/4xx → no retry.
    return False


def is_retryable_failure(*, status_code: int | None, network_error: bool = False) -> bool:
    """Unified retry decision for HTTP and transport failures."""
    if network_error or status_code is None:
        return True
    return is_retryable_http_status(status_code)
