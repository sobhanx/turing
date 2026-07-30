from __future__ import annotations

"""
Vendor-independent meeting / recording domain models.

Connectors normalize into Meeting → Recording → MediaAsset. Vendor-specific
fields stay on Meeting/Recording (and ExternalReference); MediaAsset remains
speech-input only.
"""

from django.db import models

from turing.domain.enums import MeetingStatus, RecordingStatus
from turing.models.media import UUIDModel


class Meeting(UUIDModel):
    """
    A calendar/session unit from any meeting provider.

    Identity for sync: ``(organization, provider, external_id)``.
    """

    organization = models.ForeignKey(
        "turing.Organization",
        on_delete=models.PROTECT,
        related_name="meetings",
        help_text="Owning organization (data boundary). Required.",
    )
    provider = models.CharField(
        max_length=64,
        db_index=True,
        help_text="Connector/provider code (e.g. zoom, teams, google_meet, alocom).",
    )
    external_id = models.CharField(
        max_length=255,
        db_index=True,
        help_text="Provider meeting/session id (not a recording file id).",
    )
    title = models.CharField(max_length=512, blank=True, default="")
    status = models.CharField(
        max_length=16,
        choices=MeetingStatus.choices,
        default=MeetingStatus.UNKNOWN,
        db_index=True,
    )
    scheduled_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    participants = models.JSONField(
        default=list,
        blank=True,
        help_text="Optional participant metadata list (no secrets).",
    )
    host_external_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Provider host/owner id when known.",
    )
    connector_installation = models.ForeignKey(
        "turing.ConnectorInstallation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="meetings",
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Non-indexed provider baggage (not used as the identity key).",
    )

    class Meta:
        ordering = ["-started_at", "-created_at"]
        verbose_name = "Meeting"
        verbose_name_plural = "Meetings"
        indexes = [
            models.Index(
                fields=["organization", "provider", "external_id"],
                name="turing_meeting_org_prov_ext",
            ),
            models.Index(
                fields=["organization", "-started_at"],
                name="turing_meeting_org_started",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "provider", "external_id"],
                name="turing_meeting_org_provider_ext_uniq",
            ),
        ]

    def __str__(self) -> str:
        label = self.title or self.external_id
        return f"Meeting({self.provider}:{label})"


class Recording(UUIDModel):
    """
    One media capture belonging to a Meeting.

    Links to ``MediaAsset`` after ingest. Identity:
    ``(organization, provider, external_id)`` where external_id is the
    provider recording/file id.
    """

    organization = models.ForeignKey(
        "turing.Organization",
        on_delete=models.PROTECT,
        related_name="recordings",
        help_text="Owning organization (data boundary). Required.",
    )
    meeting = models.ForeignKey(
        Meeting,
        on_delete=models.CASCADE,
        related_name="recordings",
    )
    provider = models.CharField(
        max_length=64,
        db_index=True,
        help_text="Connector/provider code (usually matches meeting.provider).",
    )
    external_id = models.CharField(
        max_length=255,
        db_index=True,
        help_text="Provider recording/file id.",
    )
    source_url = models.URLField(max_length=2048, blank=True, default="")
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=16,
        choices=RecordingStatus.choices,
        default=RecordingStatus.DISCOVERED,
        db_index=True,
    )
    media = models.OneToOneField(
        "turing.MediaAsset",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="meeting_recording",
        help_text="Turing media created from this recording (after ingest).",
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Non-indexed recording baggage (file type, size hints, etc.).",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Recording"
        verbose_name_plural = "Recordings"
        indexes = [
            models.Index(
                fields=["organization", "provider", "external_id"],
                name="turing_recording_org_prov_ext",
            ),
            models.Index(
                fields=["meeting", "-created_at"],
                name="turing_recording_meeting",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "provider", "external_id"],
                name="turing_recording_org_provider_ext_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"Recording({self.provider}:{self.external_id})"
