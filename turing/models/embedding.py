from __future__ import annotations

"""Provider-neutral embedding / index rows (Phase 4.5.3)."""

from django.db import models

from turing.models.media import UUIDModel


class EmbeddingObjectType(models.TextChoices):
    TRANSCRIPT_SEGMENT = "transcript_segment", "Transcript segment"
    TRANSCRIPT = "transcript", "Transcript"


class Embedding(UUIDModel):
    """
    Org-scoped search index row for Speech Center objects.

    ``vector`` stores a float array compatible with PostgreSQL pgvector (JSON
    list on all backends; optional SQL distance when the ``vector`` extension
    is enabled). Metadata fields remain provider-neutral.
    """

    organization = models.ForeignKey(
        "turing.Organization",
        on_delete=models.PROTECT,
        related_name="embeddings",
        db_index=True,
        help_text="Owning organization (data boundary). Required.",
    )
    object_type = models.CharField(
        max_length=64,
        choices=EmbeddingObjectType.choices,
        db_index=True,
    )
    object_id = models.CharField(max_length=64, db_index=True)
    content_hash = models.CharField(max_length=64, blank=True, default="", db_index=True)
    vector = models.JSONField(
        default=list,
        blank=True,
        help_text="pgvector-compatible embedding (list of floats).",
    )
    dimensions = models.PositiveIntegerField(
        default=0,
        help_text="Embedding dimensionality (0 = unset / null provider).",
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Embedding"
        verbose_name_plural = "Embeddings"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "object_type", "object_id"],
                name="turing_embedding_org_object_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "object_type"],
                name="turing_embed_org_type",
            ),
        ]

    def __str__(self) -> str:
        return f"Embedding({self.object_type}:{self.object_id})"
