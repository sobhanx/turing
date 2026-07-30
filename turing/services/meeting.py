from __future__ import annotations

"""
Meeting / Recording service — vendor-independent session + capture layer.

Does not download media or talk to Speechmatics. Media bytes still go through
``MediaService``; connectors keep using ``ExternalReference`` for host links.
"""

import logging
from datetime import datetime
from typing import Any

from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction
from django.utils.dateparse import parse_datetime

from turing.auth.tenancy import assert_organization_access
from turing.connectors.base import MediaPullItem
from turing.domain.enums import MeetingStatus, RecordingStatus
from turing.domain.exceptions import ValidationError
from turing.models import Meeting, MediaAsset, Organization, Recording

logger = logging.getLogger(__name__)


def _parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        # Treat as unix seconds when large enough; otherwise ignore.
        try:
            ts = float(value)
            if ts > 1_000_000_000:
                return datetime.fromtimestamp(ts)
        except (TypeError, ValueError, OSError):
            return None
        return None
    text = str(value).strip()
    if not text:
        return None
    return parse_datetime(text)


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def meeting_external_id_from_item(item: MediaPullItem) -> str:
    """
    Resolve provider meeting id from a pull item.

    Prefer explicit meeting fields; fall back to the recording external_id so
    every recording still attaches to a Meeting row (1:1 when meeting id unknown).
    """
    meta = dict(item.metadata or {})
    for key in ("meeting_id", "external_meeting_id", "meeting_external_id"):
        raw = meta.get(key)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    if getattr(item, "meeting_external_id", None):
        mid = str(item.meeting_external_id).strip()
        if mid:
            return mid
    return str(item.external_id or "").strip()


class MeetingService:
    """Create/update meetings and attach recordings (org-scoped)."""

    def upsert_meeting(
        self,
        *,
        organization: Organization,
        provider: str,
        external_id: str,
        title: str = "",
        status: str = MeetingStatus.UNKNOWN,
        scheduled_at: datetime | None = None,
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
        participants: list | None = None,
        host_external_id: str = "",
        connector_installation=None,
        metadata: dict | None = None,
        user: AbstractBaseUser | None = None,
    ) -> Meeting:
        provider = (provider or "").strip()
        external_id = (external_id or "").strip()
        if not provider:
            raise ValidationError("Meeting provider is required.")
        if not external_id:
            raise ValidationError("Meeting external_id is required.")
        if user is not None:
            assert_organization_access(
                user,
                organization,
                capability="upload_media",
            )

        defaults = {
            "title": (title or "").strip()[:512],
            "status": status or MeetingStatus.UNKNOWN,
            "scheduled_at": scheduled_at,
            "started_at": started_at,
            "ended_at": ended_at,
            "participants": list(participants or []),
            "host_external_id": (host_external_id or "").strip()[:255],
            "metadata": dict(metadata or {}),
        }
        if connector_installation is not None:
            defaults["connector_installation"] = connector_installation

        meeting, created = Meeting.objects.get_or_create(
            organization=organization,
            provider=provider,
            external_id=external_id,
            defaults=defaults,
        )
        if created:
            return meeting

        dirty: list[str] = []
        for field, value in defaults.items():
            if field == "connector_installation" and value is None:
                continue
            if field == "metadata":
                # Merge non-destructively.
                merged = dict(meeting.metadata or {})
                merged.update(value or {})
                if merged != (meeting.metadata or {}):
                    meeting.metadata = merged
                    dirty.append("metadata")
                continue
            if field == "participants" and value:
                if list(meeting.participants or []) != list(value):
                    meeting.participants = list(value)
                    dirty.append("participants")
                continue
            if field in {"title", "host_external_id"} and value and getattr(meeting, field) != value:
                setattr(meeting, field, value)
                dirty.append(field)
            elif field == "status" and value and value != MeetingStatus.UNKNOWN:
                if meeting.status != value:
                    meeting.status = value
                    dirty.append("status")
            elif field in {"scheduled_at", "started_at", "ended_at"} and value is not None:
                if getattr(meeting, field) != value:
                    setattr(meeting, field, value)
                    dirty.append(field)
            elif field == "connector_installation" and value is not None:
                if meeting.connector_installation_id != getattr(value, "id", value):
                    meeting.connector_installation = value
                    dirty.append("connector_installation")
        if dirty:
            dirty.append("updated_at")
            meeting.save(update_fields=list(dict.fromkeys(dirty)))
        return meeting

    def attach_recording(
        self,
        *,
        meeting: Meeting,
        provider: str = "",
        external_id: str,
        source_url: str = "",
        duration_ms: int | None = None,
        status: str = RecordingStatus.DISCOVERED,
        metadata: dict | None = None,
        media: MediaAsset | None = None,
        user: AbstractBaseUser | None = None,
    ) -> Recording:
        provider = (provider or meeting.provider or "").strip()
        external_id = (external_id or "").strip()
        if not provider:
            raise ValidationError("Recording provider is required.")
        if not external_id:
            raise ValidationError("Recording external_id is required.")
        if meeting.organization_id is None:
            raise ValidationError("Meeting must belong to an organization.")
        if user is not None:
            assert_organization_access(
                user,
                meeting.organization,
                capability="upload_media",
            )
        if media is not None and media.organization_id != meeting.organization_id:
            raise ValidationError("Media organization must match meeting organization.")

        defaults: dict[str, Any] = {
            "meeting": meeting,
            "source_url": (source_url or "")[:2048],
            "duration_ms": duration_ms,
            "status": status or RecordingStatus.DISCOVERED,
            "metadata": dict(metadata or {}),
        }
        if media is not None:
            defaults["media"] = media
            defaults["status"] = RecordingStatus.INGESTED

        recording, created = Recording.objects.get_or_create(
            organization=meeting.organization,
            provider=provider,
            external_id=external_id,
            defaults=defaults,
        )
        if created:
            return recording

        dirty: list[str] = []
        if recording.meeting_id != meeting.id:
            # Prefer the richer meeting if ids collide across meetings (should not).
            recording.meeting = meeting
            dirty.append("meeting")
        if source_url and recording.source_url != source_url:
            recording.source_url = source_url[:2048]
            dirty.append("source_url")
        if duration_ms is not None and recording.duration_ms != duration_ms:
            recording.duration_ms = duration_ms
            dirty.append("duration_ms")
        if media is not None and recording.media_id != media.id:
            recording.media = media
            recording.status = RecordingStatus.INGESTED
            dirty.extend(["media", "status"])
        elif status and status != recording.status and media is None:
            recording.status = status
            dirty.append("status")
        if metadata:
            merged = dict(recording.metadata or {})
            merged.update(metadata)
            if merged != (recording.metadata or {}):
                recording.metadata = merged
                dirty.append("metadata")
        if dirty:
            dirty.append("updated_at")
            recording.save(update_fields=list(dict.fromkeys(dirty)))
        return recording

    def link_media(self, recording: Recording, media: MediaAsset) -> Recording:
        if recording.organization_id != media.organization_id:
            raise ValidationError("Recording and media must share an organization.")
        recording.media = media
        recording.status = RecordingStatus.INGESTED
        if media.duration_ms and not recording.duration_ms:
            recording.duration_ms = media.duration_ms
            recording.save(update_fields=["media", "status", "duration_ms", "updated_at"])
        else:
            recording.save(update_fields=["media", "status", "updated_at"])
        return recording

    @transaction.atomic
    def ingest_from_pull_item(
        self,
        *,
        organization: Organization,
        provider: str,
        item: MediaPullItem,
        media: MediaAsset,
        connector_installation=None,
    ) -> tuple[Meeting, Recording]:
        """
        Upsert Meeting + Recording for a connector pull item and link MediaAsset.

        Safe to call repeatedly — duplicate external recording ids reuse the row.
        """
        meta = dict(item.metadata or {})
        meeting_ext = meeting_external_id_from_item(item)
        if not meeting_ext:
            raise ValidationError("Cannot resolve meeting external_id from pull item.")

        title = str(
            meta.get("topic")
            or meta.get("title")
            or meta.get("subject")
            or ""
        ).strip()
        participants = meta.get("participants")
        if participants is not None and not isinstance(participants, list):
            participants = [participants]

        status = MeetingStatus.UNKNOWN
        if meta.get("ended_at") or meta.get("recording_end"):
            status = MeetingStatus.ENDED
        elif meta.get("started_at") or meta.get("recording_start"):
            status = MeetingStatus.LIVE

        meeting = self.upsert_meeting(
            organization=organization,
            provider=provider,
            external_id=meeting_ext,
            title=title,
            status=status,
            started_at=_parse_dt(meta.get("started_at") or meta.get("recording_start")),
            ended_at=_parse_dt(meta.get("ended_at") or meta.get("recording_end")),
            scheduled_at=_parse_dt(meta.get("scheduled_at") or meta.get("start_time")),
            participants=list(participants or []),
            host_external_id=str(meta.get("host_id") or meta.get("host_external_id") or ""),
            connector_installation=connector_installation,
            metadata={
                k: v
                for k, v in meta.items()
                if k
                not in {
                    "topic",
                    "title",
                    "subject",
                    "participants",
                    "meeting_id",
                    "external_meeting_id",
                    "meeting_external_id",
                }
            },
        )
        duration_ms = _as_int(meta.get("duration_ms"))
        if duration_ms is None and media.duration_ms:
            duration_ms = media.duration_ms

        recording = self.attach_recording(
            meeting=meeting,
            provider=provider,
            external_id=str(item.external_id).strip(),
            source_url=item.source_url or "",
            duration_ms=duration_ms,
            status=RecordingStatus.INGESTED,
            metadata=meta,
            media=media,
        )
        return meeting, recording
