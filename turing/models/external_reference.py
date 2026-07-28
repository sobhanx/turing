from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models

from turing.domain.enums import ExternalReferenceTarget
from turing.models.media import UUIDModel


class ExternalReference(UUIDModel):
    """
    Stable link from a host-application object to a Turing media or transcript.

    Example: crm / deal / 12345 → MediaAsset.

    Exactly one of ``media`` or ``transcript`` must be set (no GenericForeignKey).
    """

    organization = models.ForeignKey(
        "turing.Organization",
        on_delete=models.PROTECT,
        related_name="external_references",
        help_text="Owning organization (data boundary). Required.",
    )
    external_system = models.CharField(
        max_length=64,
        db_index=True,
        help_text="Host product namespace (e.g. crm, bank, hr, meetings).",
    )
    external_type = models.CharField(
        max_length=64,
        db_index=True,
        help_text="Host object kind (e.g. deal, case, interview, meeting).",
    )
    external_id = models.CharField(
        max_length=255,
        db_index=True,
        help_text="Host object primary identifier.",
    )
    media = models.ForeignKey(
        "turing.MediaAsset",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="external_references",
    )
    transcript = models.ForeignKey(
        "turing.Transcript",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="external_references",
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Optional non-indexed host baggage (not used as the link key).",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "External reference"
        verbose_name_plural = "External references"
        indexes = [
            models.Index(
                fields=["organization", "external_system", "external_type", "external_id"],
                name="turing_extref_host_lookup",
            ),
            models.Index(fields=["media", "-created_at"], name="turing_extref_media"),
            models.Index(fields=["transcript", "-created_at"], name="turing_extref_transcript"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(media__isnull=False, transcript__isnull=True)
                    | models.Q(media__isnull=True, transcript__isnull=False)
                ),
                name="turing_extref_exactly_one_target",
            ),
            models.UniqueConstraint(
                fields=[
                    "organization",
                    "external_system",
                    "external_type",
                    "external_id",
                    "media",
                ],
                condition=models.Q(media__isnull=False),
                name="turing_extref_media_host_uniq",
            ),
            models.UniqueConstraint(
                fields=[
                    "organization",
                    "external_system",
                    "external_type",
                    "external_id",
                    "transcript",
                ],
                condition=models.Q(transcript__isnull=False),
                name="turing_extref_transcript_host_uniq",
            ),
        ]

    def __str__(self) -> str:
        target = self.target_kind
        target_id = self.media_id or self.transcript_id
        return (
            f"ExternalReference({self.external_system}/{self.external_type}/"
            f"{self.external_id} → {target}:{target_id})"
        )

    @property
    def target_kind(self) -> str:
        if self.media_id:
            return ExternalReferenceTarget.MEDIA
        if self.transcript_id:
            return ExternalReferenceTarget.TRANSCRIPT
        return ""

    @property
    def target(self):
        if self.media_id:
            return self.media
        if self.transcript_id:
            return self.transcript
        return None

    def clean(self) -> None:
        super().clean()
        has_media = self.media_id is not None
        has_transcript = self.transcript_id is not None
        if has_media == has_transcript:
            raise ValidationError(
                "Exactly one of 'media' or 'transcript' must be set."
            )
        if has_media and self.organization_id:
            media_org_id = (
                self.media.organization_id
                if self.media is not None
                else None
            )
            if media_org_id is not None and media_org_id != self.organization_id:
                raise ValidationError(
                    {"organization": "Must match the linked media organization."}
                )
        if has_transcript and self.organization_id:
            transcript_org_id = (
                self.transcript.organization_id
                if self.transcript is not None
                else None
            )
            if transcript_org_id is not None and transcript_org_id != self.organization_id:
                raise ValidationError(
                    {"organization": "Must match the linked transcript organization."}
                )
