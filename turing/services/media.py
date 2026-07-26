from __future__ import annotations

import hashlib
import logging
from typing import BinaryIO

from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction

from turing.conf import get_turing_settings
from turing.domain.enums import SourceType, StorageBackend, UseCase
from turing.domain.exceptions import ValidationError
from turing.media.metadata import extract_audio_metadata
from turing.media.validation import validate_audio_upload
from turing.models import MediaAsset
from turing.storage.media import MediaStorageService

logger = logging.getLogger(__name__)


class MediaService:
    """Register and manage media inputs for the speech engine."""

    def __init__(self, storage: MediaStorageService | None = None) -> None:
        self.storage = storage or MediaStorageService()

    def create_from_upload(
        self,
        *,
        uploaded_file: BinaryIO,
        filename: str,
        content_type: str = "",
        use_case: str = UseCase.GENERIC,
        uploaded_by: AbstractBaseUser | None = None,
        tenant_key: str = "",
        metadata: dict | None = None,
    ) -> MediaAsset:
        settings = get_turing_settings()
        raw = uploaded_file.read() if hasattr(uploaded_file, "read") else bytes(uploaded_file)
        ext, resolved_type = validate_audio_upload(
            filename=filename,
            content_type=content_type,
            byte_size=len(raw),
        )
        checksum = hashlib.sha256(raw).hexdigest()

        object_key = self.storage.save_upload(filename=filename, content=raw)
        audio_meta = extract_audio_metadata(
            raw, filename=filename, content_type=resolved_type
        )

        meta = dict(metadata or {})
        if audio_meta.duration_ms is None and not audio_meta.sample_rate_hz:
            meta.setdefault("metadata_extraction", "failed_or_unavailable")

        asset = MediaAsset(
            source_type=SourceType.UPLOAD,
            use_case=use_case,
            storage_backend=self.storage.backend_code or settings.storage_backend or StorageBackend.LOCAL,
            original_filename=filename,
            content_type=resolved_type,
            byte_size=len(raw),
            checksum=checksum,
            uploaded_by=uploaded_by,
            tenant_key=tenant_key or "",
            metadata=meta,
            object_key=object_key,
            duration_ms=audio_meta.duration_ms,
            sample_rate_hz=audio_meta.sample_rate_hz,
            channels=audio_meta.channels,
            audio_format=audio_meta.audio_format or ext,
            audio_codec=audio_meta.audio_codec,
        )
        # Keep FileField in sync for Admin/Django compatibility (same storage backend).
        asset.file.name = object_key
        asset.save()
        return asset

    def create_from_url(
        self,
        *,
        url: str,
        use_case: str = UseCase.GENERIC,
        uploaded_by: AbstractBaseUser | None = None,
        tenant_key: str = "",
        original_filename: str = "",
        metadata: dict | None = None,
    ) -> MediaAsset:
        if not url:
            raise ValidationError("External URL is required.")
        settings = get_turing_settings()
        return MediaAsset.objects.create(
            source_type=SourceType.URL,
            use_case=use_case,
            storage_backend=settings.storage_backend or StorageBackend.LOCAL,
            external_url=url,
            original_filename=original_filename or url.rsplit("/", 1)[-1],
            uploaded_by=uploaded_by,
            tenant_key=tenant_key or "",
            metadata=metadata or {},
        )

    def enrich_uploaded_asset(self, asset: MediaAsset) -> MediaAsset:
        """
        Validate + enrich a MediaAsset saved via Admin/FileField.

        Safe to call after Admin upload; does not break if metadata fails.
        """
        if not asset.file and not asset.object_key:
            return asset

        filename = asset.original_filename or (asset.file.name if asset.file else "") or "audio"
        try:
            data = self.storage.read_bytes(asset) if self.storage.exists(asset) else b""
            if not data and asset.file:
                asset.file.open("rb")
                try:
                    data = asset.file.read()
                finally:
                    asset.file.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read media %s for enrichment: %s", asset.id, exc)
            return asset

        try:
            ext, resolved_type = validate_audio_upload(
                filename=filename,
                content_type=asset.content_type,
                byte_size=len(data) or asset.byte_size,
            )
        except ValidationError:
            raise

        asset.content_type = resolved_type or asset.content_type
        asset.byte_size = len(data) or asset.byte_size
        if data:
            asset.checksum = hashlib.sha256(data).hexdigest()
        if asset.file and asset.file.name:
            asset.object_key = asset.object_key or asset.file.name
        asset.storage_backend = asset.storage_backend or self.storage.backend_code

        audio_meta = extract_audio_metadata(
            data, filename=filename, content_type=asset.content_type
        )
        if audio_meta.duration_ms is not None:
            asset.duration_ms = audio_meta.duration_ms
        if audio_meta.sample_rate_hz is not None:
            asset.sample_rate_hz = audio_meta.sample_rate_hz
        if audio_meta.channels is not None:
            asset.channels = audio_meta.channels
        if audio_meta.audio_format:
            asset.audio_format = audio_meta.audio_format
        elif ext:
            asset.audio_format = ext
        if audio_meta.audio_codec:
            asset.audio_codec = audio_meta.audio_codec

        asset.save(
            update_fields=[
                "content_type",
                "byte_size",
                "checksum",
                "object_key",
                "storage_backend",
                "duration_ms",
                "sample_rate_hz",
                "channels",
                "audio_format",
                "audio_codec",
                "updated_at",
            ]
        )
        return asset

    def read_bytes(self, asset: MediaAsset) -> bytes:
        return self.storage.read_bytes(asset)

    @transaction.atomic
    def get(self, media_id) -> MediaAsset:
        return MediaAsset.objects.get(pk=media_id)
