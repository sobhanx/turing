from __future__ import annotations

from django.db import models

from turing.domain.enums import AnalysisType
from turing.models.media import UUIDModel


class TranscriptAnalysis(UUIDModel):
    """
    Append-only derived AI output linked to a transcript.

    Raw transcript content is never modified; each analysis run creates a new row.
    """

    transcript = models.ForeignKey(
        "turing.Transcript",
        on_delete=models.CASCADE,
        related_name="analyses",
    )
    organization = models.ForeignKey(
        "turing.Organization",
        on_delete=models.PROTECT,
        related_name="transcript_analyses",
        help_text="Copied from transcript at persist time for tenant-safe queries.",
    )
    analysis_type = models.CharField(
        max_length=32,
        choices=AnalysisType.choices,
        db_index=True,
    )
    content = models.JSONField(
        default=dict,
        help_text="Provider-specific structured output (summary, action items, topics, etc.).",
    )
    provider = models.CharField(max_length=64, db_index=True)
    model_name = models.CharField(max_length=128, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["transcript", "analysis_type", "-created_at"]),
            models.Index(fields=["organization", "analysis_type"]),
        ]
        verbose_name = "Transcript analysis"
        verbose_name_plural = "Transcript analyses"

    def __str__(self) -> str:
        return f"TranscriptAnalysis({self.analysis_type} {self.transcript_id})"
