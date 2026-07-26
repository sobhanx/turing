from __future__ import annotations

from django.conf import settings
from django.db import models

from turing.domain.enums import RevisionSource, TranscriptStatus
from turing.models.media import UUIDModel


class Transcript(UUIDModel):
    """Authoritative transcript document produced by an STT job (then edited by humans)."""

    job = models.OneToOneField(
        "turing.ProcessingJob",
        on_delete=models.CASCADE,
        related_name="transcript",
    )
    media = models.ForeignKey(
        "turing.MediaAsset",
        on_delete=models.CASCADE,
        related_name="transcripts",
    )
    language_code = models.CharField(max_length=16, blank=True, default="")
    status = models.CharField(
        max_length=16,
        choices=TranscriptStatus.choices,
        default=TranscriptStatus.DRAFT,
        db_index=True,
    )
    full_text = models.TextField(blank=True, default="")
    version = models.PositiveIntegerField(default=1)
    is_primary = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Marks the current primary transcript for the media asset.",
    )
    confidence_avg = models.FloatField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="turing_approved_transcripts",
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["media", "status"]),
            models.Index(fields=["status", "-updated_at"]),
            models.Index(fields=["is_primary", "media"]),
        ]
        verbose_name = "Transcript"
        verbose_name_plural = "Transcripts"

    def __str__(self) -> str:
        return f"Transcript({self.id} v{self.version} {self.status})"


class Speaker(UUIDModel):
    """Speaker label within a transcript (diarization + human renaming)."""

    transcript = models.ForeignKey(
        Transcript,
        on_delete=models.CASCADE,
        related_name="speakers",
    )
    label = models.CharField(max_length=64)
    display_name = models.CharField(max_length=128, blank=True, default="")
    external_speaker_id = models.CharField(max_length=128, blank=True, default="")
    confidence = models.FloatField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["label"]
        unique_together = [("transcript", "label")]
        verbose_name = "Speaker"
        verbose_name_plural = "Speakers"

    def __str__(self) -> str:
        return self.display_name or self.label

    @property
    def resolved_name(self) -> str:
        return self.display_name or self.label


class TranscriptSegment(UUIDModel):
    """Timed utterance / sentence within a transcript."""

    transcript = models.ForeignKey(
        Transcript,
        on_delete=models.CASCADE,
        related_name="segments",
    )
    speaker = models.ForeignKey(
        Speaker,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="segments",
    )
    sequence = models.PositiveIntegerField()
    start_ms = models.PositiveIntegerField(default=0)
    end_ms = models.PositiveIntegerField(default=0)
    text = models.TextField()
    confidence = models.FloatField(null=True, blank=True)
    words = models.JSONField(
        default=list,
        blank=True,
        help_text="Optional word-level timings: [{text, start_ms, end_ms, confidence}].",
    )
    provider_payload = models.JSONField(default=dict, blank=True)
    is_edited = models.BooleanField(default=False)

    class Meta:
        ordering = ["sequence"]
        unique_together = [("transcript", "sequence")]
        indexes = [
            models.Index(fields=["transcript", "start_ms"]),
            models.Index(fields=["speaker"]),
        ]
        verbose_name = "Transcript segment"
        verbose_name_plural = "Transcript segments"

    def __str__(self) -> str:
        preview = self.text[:60] + ("…" if len(self.text) > 60 else "")
        return f"[{self.start_ms}-{self.end_ms}ms] {preview}"


class TranscriptRevision(UUIDModel):
    """Immutable revision snapshot for human/provider/system changes."""

    transcript = models.ForeignKey(
        Transcript,
        on_delete=models.CASCADE,
        related_name="revisions",
    )
    revision_number = models.PositiveIntegerField()
    source = models.CharField(
        max_length=16,
        choices=RevisionSource.choices,
        default=RevisionSource.HUMAN,
    )
    change_summary = models.CharField(max_length=255, blank=True, default="")
    snapshot = models.JSONField(
        default=dict,
        help_text="Full structured snapshot (speakers + segments + full_text).",
    )
    diff = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="turing_transcript_revisions",
    )

    class Meta:
        ordering = ["-revision_number"]
        unique_together = [("transcript", "revision_number")]
        verbose_name = "Transcript revision"
        verbose_name_plural = "Transcript revisions"

    def __str__(self) -> str:
        return f"Revision({self.transcript_id} #{self.revision_number})"
