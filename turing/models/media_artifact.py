from __future__ import annotations

from django.db import models

from turing.domain.enums import ArtifactKind, ArtifactStatus, StorageBackend
from turing.models.media import UUIDModel


class MediaProcessingArtifact(UUIDModel):
    """
    Derived media produced during ingestion (e.g. normalized audio for STT).

    Original ``MediaAsset`` files are never overwritten.
    """

    media = models.ForeignKey(
        "turing.MediaAsset",
        on_delete=models.CASCADE,
        related_name="processing_artifacts",
    )
    organization = models.ForeignKey(
        "turing.Organization",
        on_delete=models.PROTECT,
        related_name="media_processing_artifacts",
        help_text="Copied from media for tenant-safe queries.",
    )
    kind = models.CharField(
        max_length=32,
        choices=ArtifactKind.choices,
        default=ArtifactKind.NORMALIZED,
        db_index=True,
    )
    status = models.CharField(
        max_length=16,
        choices=ArtifactStatus.choices,
        default=ArtifactStatus.PENDING,
        db_index=True,
    )
    storage_backend = models.CharField(
        max_length=16,
        choices=StorageBackend.choices,
        default=StorageBackend.LOCAL,
    )
    object_key = models.CharField(max_length=512, blank=True, default="")
    byte_size = models.BigIntegerField(default=0)
    checksum = models.CharField(max_length=64, blank=True, default="")
    content_type = models.CharField(max_length=128, blank=True, default="")
    audio_format = models.CharField(max_length=32, blank=True, default="")
    audio_codec = models.CharField(max_length=64, blank=True, default="")
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    sample_rate_hz = models.PositiveIntegerField(null=True, blank=True)
    channels = models.PositiveSmallIntegerField(null=True, blank=True)
    probe_metadata = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["media", "kind", "-created_at"]),
            models.Index(fields=["organization", "status"]),
        ]
        verbose_name = "Media processing artifact"
        verbose_name_plural = "Media processing artifacts"

    def __str__(self) -> str:
        return f"MediaProcessingArtifact({self.kind} {self.status} {self.media_id})"
