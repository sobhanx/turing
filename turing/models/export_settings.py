"""Transcript export section visibility settings (Admin-managed)."""

from __future__ import annotations

from django.db import models, transaction

from turing.models.media import TimeStampedModel


class TranscriptExportSettings(TimeStampedModel):
    """
    Controls which sections appear in PDF/DOCX transcript exports.

    Scope today: platform-wide row (``organization`` is null).
    Future: one row per organization overrides the global defaults without
    redesigning exporters — resolve via ``resolve_for_organization``.
    """

    organization = models.ForeignKey(
        "turing.Organization",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="transcript_export_settings",
        help_text=(
            "Leave empty for platform-wide defaults. "
            "Set an organization to store a future org-level override."
        ),
    )
    is_global = models.BooleanField(
        default=True,
        editable=False,
        db_index=True,
        help_text="True when organization is empty (platform defaults).",
    )

    # Document metadata
    show_meeting_title = models.BooleanField(
        default=True,
        verbose_name="Show meeting title",
        help_text="Include the meeting / media title on the cover and meeting table.",
    )
    show_persian_date = models.BooleanField(
        default=True,
        verbose_name="Show Persian (Jalali) date",
        help_text="Show the Asia/Tehran calendar date in Jalali form.",
    )
    show_gregorian_date = models.BooleanField(
        default=True,
        verbose_name="Show Gregorian date",
        help_text="Show the Asia/Tehran Gregorian calendar date.",
    )
    show_duration = models.BooleanField(
        default=True,
        verbose_name="Show duration",
        help_text="Include media duration in cover and meeting information.",
    )
    show_speakers = models.BooleanField(
        default=True,
        verbose_name="Show speakers",
        help_text="Include speaker count / speaker list metadata.",
    )

    # Transcript
    show_full_transcript = models.BooleanField(
        default=True,
        verbose_name="Show full transcript",
        help_text="Include the transcript dialogue section.",
    )
    show_timeline = models.BooleanField(
        default=True,
        verbose_name="Show timeline timestamps",
        help_text="Show start timestamps next to each speaker turn.",
    )

    # AI sections
    show_ai_summary = models.BooleanField(
        default=False,
        verbose_name="Show executive summary",
        help_text="Include the AI executive summary section.",
    )
    show_key_topics = models.BooleanField(
        default=False,
        verbose_name="Show key topics",
        help_text="Include the AI key topics section.",
    )
    show_action_items = models.BooleanField(
        default=False,
        verbose_name="Show action items",
        help_text="Include the AI action items section.",
    )
    show_decisions = models.BooleanField(
        default=False,
        verbose_name="Show decisions",
        help_text="Include the decisions / key points section.",
    )
    show_keywords = models.BooleanField(
        default=False,
        verbose_name="Show keywords",
        help_text="Include keyword chips derived from topics.",
    )

    # Technical
    show_provider = models.BooleanField(
        default=False,
        verbose_name="Show speech provider",
        help_text="Include the STT provider code in meeting information.",
    )

    class Meta:
        verbose_name = "Transcript export settings"
        verbose_name_plural = "Transcript export settings"
        constraints = [
            models.UniqueConstraint(
                fields=["is_global"],
                condition=models.Q(is_global=True),
                name="turing_export_settings_one_global",
            ),
            models.UniqueConstraint(
                fields=["organization"],
                condition=models.Q(organization__isnull=False),
                name="turing_export_settings_one_per_org",
            ),
        ]

    def __str__(self) -> str:
        if self.organization_id:
            return f"Export settings ({self.organization})"
        return "Transcript export settings (platform)"

    def save(self, *args, **kwargs):
        self.is_global = self.organization_id is None
        super().save(*args, **kwargs)
        from django.core.cache import cache

        cache.delete("turing:transcript_export_settings:global")
        if self.organization_id:
            cache.delete(f"turing:transcript_export_settings:org:{self.organization_id}")

    @classmethod
    def get_global(cls) -> TranscriptExportSettings:
        """Return (creating if needed) the platform-wide settings row."""
        with transaction.atomic():
            obj, _ = cls.objects.get_or_create(
                is_global=True,
                defaults={"organization": None},
            )
            return obj

    @classmethod
    def resolve_for_organization(cls, organization=None) -> TranscriptExportSettings:
        """
        Resolve effective settings for an organization.

        Today returns the global row. When org overrides exist, they win.
        """
        if organization is not None:
            override = cls.objects.filter(organization=organization).first()
            if override is not None:
                return override
        return cls.get_global()
