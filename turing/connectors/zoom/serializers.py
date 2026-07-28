from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ZoomRecording:
    """Normalized Zoom cloud recording file (no secrets)."""

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


# Prefer audio; fall back to video when no audio file is present.
_PREFERRED_FILE_TYPES = ("M4A", "MP3", "WAV", "MP4")


def normalize_meeting_recordings(payload: dict[str, Any]) -> list[ZoomRecording]:
    """
    Normalize a Zoom ``GET /meetings/{id}/recordings`` (or list item) payload.

    Returns one ``ZoomRecording`` per downloadable media file we can ingest.
    """
    if not isinstance(payload, dict):
        return []

    meeting_id = str(
        payload.get("id")
        or payload.get("uuid")
        or payload.get("meeting_id")
        or ""
    ).strip()
    topic = str(payload.get("topic") or "").strip()
    files = payload.get("recording_files") or []
    if not isinstance(files, list):
        return []

    recordings: list[ZoomRecording] = []
    for raw in files:
        if not isinstance(raw, dict):
            continue
        file_type = str(raw.get("file_type") or "").strip().upper()
        status = str(raw.get("status") or "").strip().lower()
        if status and status not in {"completed", ""}:
            continue
        download_url = str(
            raw.get("download_url") or raw.get("play_url") or ""
        ).strip()
        recording_id = str(raw.get("id") or raw.get("recording_id") or "").strip()
        if not recording_id or not download_url:
            continue
        if file_type and file_type not in _PREFERRED_FILE_TYPES and file_type != "AUDIO":
            # Skip chat/transcript/timeline artifacts.
            if file_type in {"CHAT", "TRANSCRIPT", "CC", "TIMELINE", "SUMMARY"}:
                continue
        ext = str(raw.get("file_extension") or file_type or "mp4").strip().lower()
        try:
            file_size = int(raw.get("file_size") or 0)
        except (TypeError, ValueError):
            file_size = 0
        recordings.append(
            ZoomRecording(
                recording_id=recording_id,
                meeting_id=meeting_id or recording_id,
                topic=topic,
                download_url=download_url,
                file_type=file_type or "MP4",
                file_extension=ext.lstrip("."),
                file_size=file_size,
                recording_start=str(raw.get("recording_start") or ""),
                recording_end=str(raw.get("recording_end") or ""),
                metadata={
                    "meeting_id": meeting_id,
                    "topic": topic,
                    "file_type": file_type,
                    "recording_type": str(raw.get("recording_type") or ""),
                },
            )
        )
    return recordings


def normalize_recordings_list(payload: dict[str, Any]) -> list[ZoomRecording]:
    """Normalize ``GET /users/me/recordings`` style list responses."""
    if not isinstance(payload, dict):
        return []
    meetings = payload.get("meetings") or []
    if not isinstance(meetings, list):
        return []
    out: list[ZoomRecording] = []
    for meeting in meetings:
        out.extend(normalize_meeting_recordings(meeting if isinstance(meeting, dict) else {}))
    return out


def pick_primary_recording(recordings: list[ZoomRecording]) -> ZoomRecording | None:
    """Choose a single preferred file per meeting when multiple exist."""
    if not recordings:
        return None
    by_rank = {t: i for i, t in enumerate(_PREFERRED_FILE_TYPES)}
    return sorted(
        recordings,
        key=lambda r: by_rank.get(r.file_type.upper(), 99),
    )[0]
