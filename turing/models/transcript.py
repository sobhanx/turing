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
    organization = models.ForeignKey(
        "turing.Organization",
        on_delete=models.PROTECT,
        related_name="transcripts",
        help_text="Copied from job/media at persist time. Required.",
    )
    language_code = models.CharField(max_length=16, blank=True, default="")
    status = models.CharField(
        max_length=16,
        choices=TranscriptStatus.choices,
        default=TranscriptStatus.DRAFT,
        db_index=True,
        help_text="Review workflow: draft → in_review → approved.",
    )
    full_text = models.TextField(
        blank=True,
        default="",
        help_text="Denormalized searchable transcript body.",
    )
    version = models.PositiveIntegerField(default=1)
    is_primary = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Marks the current primary transcript for the media asset.",
    )
    confidence_avg = models.FloatField(null=True, blank=True)
    word_count = models.PositiveIntegerField(
        default=0,
        help_text="Cached word count from provider words or segment text.",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="turing_approved_transcripts",
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Provider-agnostic metadata bag (raw provider payload refs, etc.).",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["media", "status"]),
            models.Index(fields=["status", "-updated_at"]),
            models.Index(fields=["is_primary", "media"]),
            models.Index(fields=["organization", "status"]),
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
        constraints = [
            models.UniqueConstraint(
                fields=["transcript", "label"],
                name="turing_speaker_transcript_label_uniq",
            ),
        ]
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
        help_text=(
            "Optional provider-agnostic word list: "
            "[{text, start_ms, end_ms, confidence}, ...]. "
            "Mirrored in TranscriptWord when present."
        ),
    )
    provider_payload = models.JSONField(
        default=dict,
        blank=True,
        help_text="Opaque provider segment metadata (kept for reprocessing/debug).",
    )
    is_edited = models.BooleanField(default=False)

    class Meta:
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["transcript", "sequence"],
                name="turing_segment_transcript_sequence_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["transcript", "start_ms"]),
            models.Index(fields=["speaker"]),
        ]
        verbose_name = "Transcript segment"
        verbose_name_plural = "Transcript segments"

    def __str__(self) -> str:
        preview = self.text[:60] + ("…" if len(self.text) > 60 else "")
        return f"[{self.start_ms}-{self.end_ms}ms] {preview}"

    @property
    def word_count(self) -> int:
        if self.words:
            return len(self.words)
        return len(self.text.split()) if self.text else 0


class TranscriptWord(UUIDModel):
    """
    Structured word-level timing row (provider-agnostic).

    Prefer this for querying; ``TranscriptSegment.words`` JSON remains for
    compact API payloads and backward compatibility.
    """

    segment = models.ForeignKey(
        TranscriptSegment,
        on_delete=models.CASCADE,
        related_name="word_entries",
    )
    sequence = models.PositiveIntegerField()
    text = models.CharField(max_length=512)
    start_ms = models.PositiveIntegerField(default=0)
    end_ms = models.PositiveIntegerField(default=0)
    confidence = models.FloatField(null=True, blank=True)
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Optional extras (speaker_label, provider ids, …).",
    )

    class Meta:
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["segment", "sequence"],
                name="turing_word_segment_sequence_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["segment", "start_ms"]),
            models.Index(fields=["text"]),
        ]
        verbose_name = "Transcript word"
        verbose_name_plural = "Transcript words"

    def __str__(self) -> str:
        return f"{self.text}[{self.start_ms}-{self.end_ms}]"


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
        constraints = [
            models.UniqueConstraint(
                fields=["transcript", "revision_number"],
                name="turing_revision_transcript_number_uniq",
            ),
        ]
        verbose_name = "Transcript revision"
        verbose_name_plural = "Transcript revisions"

    def __str__(self) -> str:
        return f"Revision({self.transcript_id} #{self.revision_number})"
