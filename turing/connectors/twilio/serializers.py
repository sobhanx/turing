from __future__ import annotations

"""Twilio call / recording serializers (no secrets)."""

from typing import Any

from turing.connectors.telephony.serializers import TelephonyCall, normalize_call

EXTERNAL_SYSTEM = "twilio"


def recording_media_url(
    *,
    account_sid: str,
    recording_sid: str,
    api_base: str = "https://api.twilio.com",
    extension: str = "mp3",
) -> str:
    """Build a downloadable Twilio recording media URL (no auth embedded)."""
    base = (api_base or "https://api.twilio.com").rstrip("/")
    account_sid = (account_sid or "").strip()
    recording_sid = (recording_sid or "").strip()
    ext = (extension or "mp3").lstrip(".")
    return (
        f"{base}/2010-04-01/Accounts/{account_sid}/Recordings/"
        f"{recording_sid}.{ext}"
    )


def normalize_twilio_recording(
    recording: dict[str, Any],
    *,
    account_sid: str,
    call: dict[str, Any] | None = None,
    api_base: str = "https://api.twilio.com",
) -> TelephonyCall | None:
    """
    Normalize a Twilio Recording (+ optional Call) into ``TelephonyCall``.

    ``external_id`` is the Call SID (required for ExternalReference linking).
    """
    if not isinstance(recording, dict):
        return None

    call_sid = str(
        recording.get("call_sid")
        or recording.get("callSid")
        or (call or {}).get("sid")
        or ""
    ).strip()
    recording_sid = str(recording.get("sid") or recording.get("recording_sid") or "").strip()
    if not call_sid:
        return None

    media = str(recording.get("media_url") or recording.get("mediaUrl") or "").strip()
    if media and not media.endswith((".mp3", ".wav", ".ogg")):
        media = f"{media}.mp3"
    if not media and recording_sid and account_sid:
        media = recording_media_url(
            account_sid=account_sid,
            recording_sid=recording_sid,
            api_base=api_base,
        )
    if not media:
        return None

    call = call if isinstance(call, dict) else {}
    duration = recording.get("duration")
    if duration in (None, "") and call.get("duration") not in (None, ""):
        duration = call.get("duration")

    payload: dict[str, Any] = {
        "external_system": EXTERNAL_SYSTEM,
        "external_id": call_sid,
        "recording_url": media,
        "caller": call.get("from") or call.get("From") or recording.get("from") or "",
        "callee": call.get("to") or call.get("To") or recording.get("to") or "",
        "started_at": (
            recording.get("start_time")
            or recording.get("date_created")
            or call.get("start_time")
            or call.get("date_created")
            or ""
        ),
        "duration": duration,
        "direction": call.get("direction") or "",
        "metadata": {
            "recording_sid": recording_sid,
            "call_status": call.get("status") or "",
            "recording_status": recording.get("status") or "",
            "channels": recording.get("channels"),
            "source": recording.get("source") or "",
        },
    }
    return normalize_call(payload, external_system=EXTERNAL_SYSTEM)


def pick_primary_recording(recordings: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Prefer the longest completed recording for a call."""
    if not recordings:
        return None

    def _duration(rec: dict[str, Any]) -> int:
        try:
            return int(float(rec.get("duration") or 0))
        except (TypeError, ValueError):
            return 0

    completed = [
        r
        for r in recordings
        if str(r.get("status") or "").lower() in {"", "completed", "available"}
    ]
    pool = completed or list(recordings)
    return max(pool, key=_duration)
