"""Asia/Tehran timezone + Jalali calendar helpers for export presentation."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

TEHRAN_TZ = ZoneInfo("Asia/Tehran")


def to_tehran(value: datetime | None) -> datetime | None:
    """Convert a timestamp to Asia/Tehran without mutating stored DB values."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(TEHRAN_TZ)


def format_gregorian_date(value: datetime | None) -> str:
    local = to_tehran(value)
    if local is None:
        return "—"
    return local.strftime("%Y-%m-%d")


def format_gregorian_datetime(value: datetime | None) -> str:
    local = to_tehran(value)
    if local is None:
        return "—"
    return local.strftime("%Y-%m-%d %H:%M Asia/Tehran")


def format_persian_date(value: datetime | None) -> str:
    local = to_tehran(value)
    if local is None:
        return "—"
    jy, jm, jd = gregorian_to_jalali(local.year, local.month, local.day)
    return f"{jy:04d}/{jm:02d}/{jd:02d}"


def gregorian_to_jalali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    """
    Convert Gregorian date to Jalali (Persian) calendar.

    Pure algorithm — no third-party dependency.
    """
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = (
        355666
        + (365 * gy)
        + ((gy2 + 3) // 4)
        - ((gy2 + 99) // 100)
        + ((gy2 + 399) // 400)
        + gd
        + g_d_m[gm - 1]
    )
    jy = -1595 + 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm = 1 + days // 31
        jd = 1 + (days % 31)
    else:
        jm = 7 + (days - 186) // 30
        jd = 1 + ((days - 186) % 30)
    return jy, jm, jd
