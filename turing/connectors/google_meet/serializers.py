from __future__ import annotations

"""Google Meet / Drive recording serializers (no secrets)."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class GoogleMeetRecording:
    """Normalized Google Meet recording file (typically from Drive)."""

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


_AUDIO_MIMES = ("audio/", "mpeg", "m4a", "mp3", "wav")
_VIDEO_MIMES = ("video/", "mp4", "webm")


def _extension_from_name_mime(name: str, mime: str) -> str:
    lower = f"{name} {mime}".lower()
    for ext in ("m4a", "mp3", "wav", "mp4", "webm"):
        if name.lower().endswith(f".{ext}") or ext in lower:
            return ext
    if "audio" in lower:
        return "m4a"
    return "mp4"


def _file_type_from_mime(name: str, mime: str) -> str:
    lower = f"{name} {mime}".lower()
    if any(h in lower for h in _AUDIO_MIMES):
        return "AUDIO"
    if any(h in lower for h in _VIDEO_MIMES):
        return "VIDEO"
    return "MP4"


def normalize_recording_item(raw: dict[str, Any]) -> GoogleMeetRecording | None:
    """Normalize a Drive file or Meet recording descriptor."""
    if not isinstance(raw, dict):
        return None

    recording_id = str(
        raw.get("id") or raw.get("recording_id") or raw.get("recordingId") or ""
    ).strip()
    name = str(raw.get("name") or raw.get("title") or raw.get("topic") or "").strip()
    mime = str(raw.get("mimeType") or raw.get("contentType") or "").strip()

    download_url = str(
        raw.get("webContentLink")
        or raw.get("download_url")
        or raw.get("downloadUrl")
        or raw.get("@microsoft.graph.downloadUrl")
        or ""
    ).strip()
    # Drive API often needs files.get alt=media; allow explicit content link.
    if not download_url:
        download_url = str(raw.get("webViewLink") or "").strip()

    if not recording_id or not download_url:
        return None

    meeting_id = str(
        raw.get("meeting_id")
        or raw.get("meetingId")
        or (raw.get("appProperties") or {}).get("meetingId")
        or recording_id
    ).strip()
    try:
        file_size = int(raw.get("size") or raw.get("file_size") or 0)
    except (TypeError, ValueError):
        file_size = 0

    file_type = _file_type_from_mime(name, mime)
    ext = _extension_from_name_mime(name, mime)

    return GoogleMeetRecording(
        recording_id=recording_id,
        meeting_id=meeting_id,
        topic=name.rsplit(".", 1)[0] if name else "",
        download_url=download_url,
        file_type=file_type,
        file_extension=ext,
        file_size=file_size,
        recording_start=str(
            raw.get("createdTime") or raw.get("recording_start") or ""
        ),
        recording_end=str(raw.get("recording_end") or ""),
        metadata={
            "meeting_id": meeting_id,
            "topic": name,
            "mime_type": mime,
        },
    )


def normalize_meeting_recordings(payload: dict[str, Any]) -> list[GoogleMeetRecording]:
    """Normalize a single meeting / file list payload."""
    if not isinstance(payload, dict):
        return []

    files = payload.get("files")
    if files is None:
        files = payload.get("recordings")
    if files is None:
        files = payload.get("items")
    if files is None and payload.get("id"):
        item = normalize_recording_item(payload)
        return [item] if item else []
    if not isinstance(files, list):
        return []

    out: list[GoogleMeetRecording] = []
    for raw in files:
        item = normalize_recording_item(raw if isinstance(raw, dict) else {})
        if item:
            out.append(item)
    return out


def normalize_recordings_list(payload: dict[str, Any]) -> list[GoogleMeetRecording]:
    """Normalize Drive ``files.list`` or nested meeting list responses."""
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("meetings"), list):
        out: list[GoogleMeetRecording] = []
        for meeting in payload["meetings"]:
            if isinstance(meeting, dict):
                out.extend(normalize_meeting_recordings(meeting))
        return out
    return normalize_meeting_recordings(payload)


def pick_primary_recording(
    recordings: list[GoogleMeetRecording],
) -> GoogleMeetRecording | None:
    """Prefer audio over video when multiple files exist for a meeting."""
    if not recordings:
        return None
    rank = {"AUDIO": 0, "M4A": 0, "MP3": 1, "WAV": 2, "VIDEO": 3, "MP4": 4}
    return sorted(
        recordings,
        key=lambda r: rank.get(r.file_type.upper(), 50),
    )[0]
