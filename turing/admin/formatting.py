from __future__ import annotations

"""Admin presentation helpers (no schema / API impact)."""


def format_timestamp_ms(ms: int | float | None) -> str:
    """
    Format milliseconds as ``HH:MM:SS.mmm``.

    Examples:
        0 -> 00:00:00.000
        76785 -> 00:01:16.785
        3723123 -> 01:02:03.123
    """
    if ms is None:
        return "—"
    total = max(0, int(ms))
    hours, rem = divmod(total, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def format_confidence_pct(value: float | None) -> str:
    """Format a 0–1 confidence score as a percentage, or ``—`` when missing."""
    if value is None:
        return "—"
    try:
        pct = float(value) * 100.0
    except (TypeError, ValueError):
        return "—"
    return f"{pct:.1f}%"
