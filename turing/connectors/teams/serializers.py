from __future__ import annotations

"""Microsoft Teams / Graph recording serializers (no secrets)."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TeamsRecording:
    """Normalized Teams / Graph meeting recording (no secrets)."""

    recording_id: str
    meeting_id: str
    topic: str = ""
    download_url: str = ""
    file_type: str = ""
    file_extension: str = ""
    file_size: int = 0
    recording_start: str = ""
    recording_end: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


_AUDIO_HINTS = ("audio", "m4a", "mp3", "wav")
_VIDEO_HINTS = ("video", "mp4", "webm")


def _extension_from_url_or_type(url: str, content_type: str) -> str:
    lower = f"{url} {content_type}".lower()
    for ext in ("m4a", "mp3", "wav", "mp4", "webm"):
        if ext in lower:
            return ext
    if "audio" in lower:
        return "m4a"
    return "mp4"


def _file_type_from_hints(url: str, content_type: str) -> str:
    lower = f"{url} {content_type}".lower()
    if any(h in lower for h in _AUDIO_HINTS):
        return "AUDIO"
    if any(h in lower for h in _VIDEO_HINTS):
        return "VIDEO"
    return "MP4"


def normalize_recording_item(
    raw: dict[str, Any],
    *,
    meeting_id: str = "",
    topic: str = "",
) -> TeamsRecording | None:
    """Normalize a single Graph recording object."""
    if not isinstance(raw, dict):
        return None
    recording_id = str(
        raw.get("id") or raw.get("recording_id") or raw.get("recordingId") or ""
    ).strip()
    download_url = str(
        raw.get("recordingContentUrl")
        or raw.get("contentUrl")
        or raw.get("download_url")
        or raw.get("downloadUrl")
        or raw.get("@microsoft.graph.downloadUrl")
        or ""
    ).strip()
    if not recording_id or not download_url:
        return None

    mid = str(
        meeting_id
        or raw.get("meetingId")
        or raw.get("meeting_id")
        or raw.get("onlineMeetingId")
        or ""
    ).strip() or recording_id
    subject = str(
        topic or raw.get("subject") or raw.get("topic") or raw.get("displayName") or ""
    ).strip()
    content_type = str(raw.get("contentType") or raw.get("file_type") or "").strip()
    file_type = _file_type_from_hints(download_url, content_type)
    ext = _extension_from_url_or_type(download_url, content_type)
    try:
        file_size = int(raw.get("file_size") or raw.get("size") or 0)
    except (TypeError, ValueError):
        file_size = 0

    return TeamsRecording(
        recording_id=recording_id,
        meeting_id=mid,
        topic=subject,
        download_url=download_url,
        file_type=file_type,
        file_extension=ext,
        file_size=file_size,
        recording_start=str(
            raw.get("createdDateTime")
            or raw.get("recording_start")
            or raw.get("startDateTime")
            or ""
        ),
        recording_end=str(raw.get("recording_end") or raw.get("endDateTime") or ""),
        metadata={
            "meeting_id": mid,
            "topic": subject,
            "content_type": content_type,
        },
    )


def normalize_meeting_recordings(
    payload: dict[str, Any],
    *,
    meeting_id: str = "",
    topic: str = "",
) -> list[TeamsRecording]:
    """
    Normalize recordings for one online meeting.

    Accepts Graph ``{ "value": [ ... ] }`` or a meeting object with nested
    ``recordings``.
    """
    if not isinstance(payload, dict):
        return []

    mid = str(
        meeting_id
        or payload.get("id")
        or payload.get("meetingId")
        or payload.get("meeting_id")
        or ""
    ).strip()
    subject = str(
        topic or payload.get("subject") or payload.get("topic") or ""
    ).strip()

    raw_list = payload.get("value")
    if raw_list is None:
        raw_list = payload.get("recordings") or []
    if not isinstance(raw_list, list):
        # Single recording object
        item = normalize_recording_item(payload, meeting_id=mid, topic=subject)
        return [item] if item else []

    out: list[TeamsRecording] = []
    for raw in raw_list:
        item = normalize_recording_item(
            raw if isinstance(raw, dict) else {},
            meeting_id=mid,
            topic=subject,
        )
        if item:
            out.append(item)
    return out


def normalize_recordings_list(payload: dict[str, Any]) -> list[TeamsRecording]:
    """
    Normalize a list payload of meetings and/or flat recordings.

    Supports:
    - ``{ "value": [ {meeting with recordings}, ... ] }``
    - ``{ "value": [ {recording}, ... ] }``
    - ``{ "meetings": [ ... ] }``
    """
    if not isinstance(payload, dict):
        return []

    meetings = payload.get("meetings")
    if isinstance(meetings, list):
        out: list[TeamsRecording] = []
        for meeting in meetings:
            if isinstance(meeting, dict):
                out.extend(normalize_meeting_recordings(meeting))
        return out

    values = payload.get("value")
    if not isinstance(values, list):
        return normalize_meeting_recordings(payload)

    out = []
    for entry in values:
        if not isinstance(entry, dict):
            continue
        if "recordings" in entry or "subject" in entry and "recordingContentUrl" not in entry:
            out.extend(normalize_meeting_recordings(entry))
        else:
            item = normalize_recording_item(entry)
            if item:
                out.append(item)
            else:
                out.extend(normalize_meeting_recordings(entry))
    return out


def pick_primary_recording(recordings: list[TeamsRecording]) -> TeamsRecording | None:
    """Prefer audio over video when multiple recordings exist for a meeting."""
    if not recordings:
        return None
    rank = {"AUDIO": 0, "M4A": 0, "MP3": 1, "WAV": 2, "VIDEO": 3, "MP4": 4}
    return sorted(
        recordings,
        key=lambda r: rank.get(r.file_type.upper(), 50),
    )[0]
