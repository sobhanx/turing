from __future__ import annotations

"""Salesforce CRM call/meeting recording serializers (no secrets)."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SalesforceRecording:
    """Normalized Salesforce call/meeting record with optional media URL."""

    recording_id: str
    external_type: str  # "call" | "meeting"
    topic: str = ""
    download_url: str = ""
    file_type: str = ""
    file_extension: str = ""
    file_size: int = 0
    recording_start: str = ""
    recording_end: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def _detect_external_type(raw: dict[str, Any]) -> str:
    explicit = str(
        raw.get("external_type")
        or raw.get("Type")
        or raw.get("CallType")
        or raw.get("ActivityType")
        or ""
    ).strip().lower()
    if explicit in {"meeting", "event", "conference"}:
        return "meeting"
    if explicit in {"call", "voicecall", "voice"}:
        return "call"
    sobject = str(raw.get("attributes", {}).get("type") or raw.get("sobject") or "").lower()
    if "meeting" in sobject or "event" in sobject:
        return "meeting"
    if "call" in sobject or "voice" in sobject or "task" in sobject:
        return "call"
    # Default CRM voice/activity to call.
    return "call"


def _recording_url(raw: dict[str, Any]) -> str:
    return str(
        raw.get("media_url")
        or raw.get("RecordingUrl")
        or raw.get("RecordingURL")
        or raw.get("CallRecording")
        or raw.get("Recording_Link__c")
        or raw.get("MediaUrl__c")
        or raw.get("download_url")
        or raw.get("downloadUrl")
        or ""
    ).strip()


def normalize_record(raw: dict[str, Any]) -> SalesforceRecording | None:
    """Normalize a single Salesforce SOQL/API record that may include a recording."""
    if not isinstance(raw, dict):
        return None

    recording_id = str(
        raw.get("Id")
        or raw.get("id")
        or raw.get("external_id")
        or raw.get("recording_id")
        or ""
    ).strip()
    download_url = _recording_url(raw)
    if not recording_id or not download_url:
        return None

    external_type = _detect_external_type(raw)
    topic = str(
        raw.get("Subject")
        or raw.get("Name")
        or raw.get("topic")
        or raw.get("Description")
        or ""
    ).strip()
    ext = "mp3"
    lower = download_url.lower()
    for candidate in ("m4a", "mp3", "wav", "mp4", "webm"):
        if candidate in lower:
            ext = candidate
            break
    file_type = "AUDIO" if ext in {"m4a", "mp3", "wav"} else "VIDEO"

    try:
        file_size = int(raw.get("file_size") or raw.get("CallDurationInSeconds") or 0)
    except (TypeError, ValueError):
        file_size = 0

    return SalesforceRecording(
        recording_id=recording_id,
        external_type=external_type,
        topic=topic,
        download_url=download_url,
        file_type=file_type,
        file_extension=ext,
        file_size=file_size,
        recording_start=str(
            raw.get("CreatedDate")
            or raw.get("ActivityDate")
            or raw.get("recording_start")
            or ""
        ),
        recording_end=str(raw.get("recording_end") or ""),
        metadata={
            "external_type": external_type,
            "topic": topic,
            "who_id": str(raw.get("WhoId") or ""),
            "what_id": str(raw.get("WhatId") or ""),
            "owner_id": str(raw.get("OwnerId") or ""),
            "sobject": str(
                (raw.get("attributes") or {}).get("type") or raw.get("sobject") or ""
            ),
        },
    )


def normalize_query_records(payload: dict[str, Any]) -> list[SalesforceRecording]:
    """Normalize a Salesforce query response ``{ "records": [ ... ] }``."""
    if not isinstance(payload, dict):
        return []
    records = payload.get("records")
    if records is None:
        records = payload.get("value") or payload.get("items") or []
    if not isinstance(records, list):
        item = normalize_record(payload)
        return [item] if item else []
    out: list[SalesforceRecording] = []
    for raw in records:
        item = normalize_record(raw if isinstance(raw, dict) else {})
        if item:
            out.append(item)
    return out


def normalize_recordings_list(payload: dict[str, Any]) -> list[SalesforceRecording]:
    """Alias for query/list normalization."""
    return normalize_query_records(payload)
