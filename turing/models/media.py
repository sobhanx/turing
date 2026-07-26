from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from turing.domain.enums import StorageBackend, SourceType


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class UUIDModel(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class MediaAsset(UUIDModel):
    """
    Input media for the speech engine.

    Separated from ProcessingJob so the same file can be reprocessed with
    different providers, languages, or options.
    """

    source_type = models.CharField(
        max_length=16,
        choices=SourceType.choices,
        default=SourceType.UPLOAD,
        db_index=True,
    )
    use_case = models.CharField(
        max_length=32,
        default="generic",
        db_index=True,
        help_text="Product scenario label (meeting, crm_call, interview, voice_file).",
    )
    storage_backend = models.CharField(
        max_length=16,
        choices=StorageBackend.choices,
        default=StorageBackend.LOCAL,
    )
    file = models.FileField(upload_to="turing/media/%Y/%m/", blank=True, null=True)
    object_key = models.CharField(max_length=512, blank=True, default="")
    original_filename = models.CharField(max_length=255, blank=True, default="")
    content_type = models.CharField(max_length=128, blank=True, default="")
    byte_size = models.BigIntegerField(default=0)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    checksum = models.CharField(max_length=64, blank=True, default="", db_index=True)
    external_url = models.URLField(max_length=2048, blank=True, default="")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="turing_media_assets",
    )
    tenant_key = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_index=True,
        help_text="Optional logical tenant / host-project isolation key.",
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["uploaded_by", "-created_at"]),
            models.Index(fields=["tenant_key", "-created_at"]),
            models.Index(fields=["use_case", "-created_at"]),
        ]
        verbose_name = "Media asset"
        verbose_name_plural = "Media assets"

    def __str__(self) -> str:
        label = self.original_filename or self.external_url or str(self.id)
        return f"MediaAsset({label})"

    @property
    def display_name(self) -> str:
        return self.original_filename or self.object_key or self.external_url or str(self.id)
