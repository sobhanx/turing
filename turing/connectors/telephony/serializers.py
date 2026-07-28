from __future__ import annotations

"""Telephony call recording serializers (Phase 4.4.3). Never includes secrets."""

from dataclasses import dataclass, field
from typing import Any


EXTERNAL_TYPE_CALL = "call"
DEFAULT_EXTERNAL_SYSTEM = "telephony"

_SECRET_META_KEYS = frozenset(
    {
        "api_token",
        "api_key",
        "token",
        "secret",
        "password",
        "access_token",
        "refresh_token",
        "authorization",
    }
)


@dataclass(frozen=True)
class TelephonyCall:
    """
    Normalized telephony call with optional recording URL.

    Shape used by ``TelephonyConnector`` sync → MediaService ingest.
    """

    external_system: str
    external_type: str  # always "call"
    external_id: str
    recording_url: str
    caller: str = ""
    callee: str = ""
    started_at: str = ""
    duration: int | float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_public_dict(self) -> dict[str, Any]:
        """Public normalized payload (no secrets)."""
        return {
            "external_system": self.external_system,
            "external_type": self.external_type,
            "external_id": self.external_id,
            "recording_url": self.recording_url,
            "caller": self.caller,
            "callee": self.callee,
            "started_at": self.started_at,
            "duration": self.duration,
            "metadata": dict(self.metadata or {}),
        }


def _scrub_metadata(raw: dict[str, Any] | None) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in dict(raw or {}).items():
        key_l = str(key).lower()
        if key_l in _SECRET_META_KEYS or any(
            fragment in key_l for fragment in ("secret", "token", "password")
        ):
            continue
        cleaned[key] = value
    return cleaned


def _recording_url(raw: dict[str, Any]) -> str:
    return str(
        raw.get("recording_url")
        or raw.get("recordingUrl")
        or raw.get("media_url")
        or raw.get("mediaUrl")
        or raw.get("download_url")
        or raw.get("downloadUrl")
        or raw.get("RecordingUrl")
        or ""
    ).strip()


def _duration(raw: dict[str, Any]) -> int | float | None:
    value = raw.get("duration")
    if value is None:
        value = raw.get("duration_seconds")
    if value is None:
        value = raw.get("Duration")
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number.is_integer():
        return int(number)
    return number


def normalize_call(
    raw: dict[str, Any],
    *,
    external_system: str = DEFAULT_EXTERNAL_SYSTEM,
) -> TelephonyCall | None:
    """
    Normalize a vendor call payload into ``TelephonyCall``.

    Returns ``None`` when ``external_id`` or ``recording_url`` is missing.
    """
    if not isinstance(raw, dict):
        return None

    external_id = str(
        raw.get("external_id")
        or raw.get("call_id")
        or raw.get("callId")
        or raw.get("id")
        or raw.get("Id")
        or ""
    ).strip()
    recording_url = _recording_url(raw)
    if not external_id or not recording_url:
        return None

    system = str(
        raw.get("external_system") or external_system or DEFAULT_EXTERNAL_SYSTEM
    ).strip() or DEFAULT_EXTERNAL_SYSTEM

    meta = _scrub_metadata(raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {})
    # Preserve useful non-secret top-level extras in metadata when absent.
    for key in ("direction", "queue", "agent_id", "disposition", "topic"):
        if key in raw and key not in meta:
            meta[key] = raw[key]

    return TelephonyCall(
        external_system=system,
        external_type=EXTERNAL_TYPE_CALL,
        external_id=external_id,
        recording_url=recording_url,
        caller=str(raw.get("caller") or raw.get("from") or raw.get("From") or "").strip(),
        callee=str(raw.get("callee") or raw.get("to") or raw.get("To") or "").strip(),
        started_at=str(
            raw.get("started_at")
            or raw.get("start_time")
            or raw.get("startedAt")
            or raw.get("CreatedDate")
            or ""
        ).strip(),
        duration=_duration(raw),
        metadata=meta,
    )


def normalize_calls(
    records: list[dict[str, Any]] | None,
    *,
    external_system: str = DEFAULT_EXTERNAL_SYSTEM,
) -> list[TelephonyCall]:
    """Normalize a list of vendor call payloads, dropping invalid rows."""
    calls: list[TelephonyCall] = []
    for raw in records or []:
        call = normalize_call(raw, external_system=external_system)
        if call is not None:
            calls.append(call)
    return calls
