from __future__ import annotations

import hashlib
import logging
from typing import BinaryIO

from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction

from turing.auth.tenancy import resolve_organization
from turing.conf import get_turing_settings
from turing.domain.enums import SourceType, StorageBackend, UseCase
from turing.domain.exceptions import ValidationError
from turing.media.metadata import extract_audio_metadata_from_path
from turing.media.validation import validate_audio_upload
from turing.models import MediaAsset, Organization
from turing.storage.media import MediaStorageService
from turing.storage.spool import spool_upload

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
        organization: Organization | None = None,
        organization_id=None,
        metadata: dict | None = None,
    ) -> MediaAsset:
        """
        Register an uploaded audio file.

        Large uploads are spooled to disk (hashed + size-checked) then streamed
        into the active storage backend — avoiding a full in-memory copy.
        """
        settings = get_turing_settings()
        with spool_upload(uploaded_file, max_bytes=settings.max_upload_bytes) as spool:
            ext, resolved_type = validate_audio_upload(
                filename=filename,
                content_type=content_type,
                byte_size=spool.size,
            )
            with spool.open("rb") as handle:
                object_key = self.storage.save_upload(
                    filename=filename,
                    content=handle,
                    content_type=resolved_type,
                )
            audio_meta = extract_audio_metadata_from_path(
                spool.path,
                filename=filename,
                content_type=resolved_type,
            )
            checksum = spool.checksum
            byte_size = spool.size

        meta = dict(metadata or {})
        if audio_meta.duration_ms is None and not audio_meta.sample_rate_hz:
            meta.setdefault("metadata_extraction", "failed_or_unavailable")

        org = resolve_organization(
            organization=organization,
            organization_id=organization_id,
            tenant_key=tenant_key,
            user=uploaded_by,
            capability="upload_media",
        )
        resolved_tenant = (tenant_key or "").strip() or org.slug or ""

        asset = MediaAsset(
            source_type=SourceType.UPLOAD,
            use_case=use_case,
            storage_backend=self.storage.backend_code
            or settings.storage_backend
            or StorageBackend.LOCAL,
            original_filename=filename,
            content_type=resolved_type,
            byte_size=byte_size,
            checksum=checksum,
            uploaded_by=uploaded_by,
            organization=org,
            tenant_key=resolved_tenant,
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
        organization: Organization | None = None,
        organization_id=None,
        original_filename: str = "",
        metadata: dict | None = None,
    ) -> MediaAsset:
        if not url:
            raise ValidationError("External URL is required.")
        settings = get_turing_settings()
        org = resolve_organization(
            organization=organization,
            organization_id=organization_id,
            tenant_key=tenant_key,
            user=uploaded_by,
            capability="upload_media",
        )
        return MediaAsset.objects.create(
            source_type=SourceType.URL,
            use_case=use_case,
            storage_backend=settings.storage_backend or StorageBackend.LOCAL,
            external_url=url,
            original_filename=original_filename or url.rsplit("/", 1)[-1],
            uploaded_by=uploaded_by,
            organization=org,
            tenant_key=(tenant_key or "").strip() or org.slug or "",
            metadata=metadata or {},
        )

    def ensure_organization(self, asset: MediaAsset) -> MediaAsset:
        """Assign Default organization when Admin uploads omit it."""
        if asset.organization_id:
            return asset
        asset.organization = Organization.get_default()
        if not asset.tenant_key:
            asset.tenant_key = asset.organization.slug
        asset.save(update_fields=["organization", "tenant_key", "updated_at"])
        return asset

    def enrich_uploaded_asset(self, asset: MediaAsset) -> MediaAsset:
        """
        Validate + enrich a MediaAsset saved via Admin/FileField.

        Safe to call after Admin upload; does not break if metadata fails.
        Streams from storage when possible (spools to temp for hash/metadata).
        """
        if not asset.file and not asset.object_key:
            return asset

        filename = asset.original_filename or (asset.file.name if asset.file else "") or "audio"
        settings = get_turing_settings()
        try:
            if self.storage.exists(asset):
                with self.storage.open(asset) as handle:
                    with spool_upload(handle, max_bytes=settings.max_upload_bytes) as spool:
                        return self._apply_enrichment(asset, filename=filename, spool=spool)
            if asset.file:
                asset.file.open("rb")
                try:
                    with spool_upload(asset.file, max_bytes=settings.max_upload_bytes) as spool:
                        return self._apply_enrichment(asset, filename=filename, spool=spool)
                finally:
                    asset.file.close()
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read media %s for enrichment: %s", asset.id, exc)
            return asset
        return asset

    def _apply_enrichment(self, asset: MediaAsset, *, filename: str, spool) -> MediaAsset:
        try:
            ext, resolved_type = validate_audio_upload(
                filename=filename,
                content_type=asset.content_type,
                byte_size=spool.size or asset.byte_size,
            )
        except ValidationError:
            raise

        asset.content_type = resolved_type or asset.content_type
        asset.byte_size = spool.size or asset.byte_size
        asset.checksum = spool.checksum or asset.checksum
        if asset.file and asset.file.name:
            asset.object_key = asset.object_key or asset.file.name
        asset.storage_backend = asset.storage_backend or self.storage.backend_code

        if not asset.organization_id:
            asset.organization = Organization.get_default()
            if not asset.tenant_key:
                asset.tenant_key = asset.organization.slug

        audio_meta = extract_audio_metadata_from_path(
            spool.path, filename=filename, content_type=asset.content_type
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
                "organization",
                "tenant_key",
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

    def open(self, asset: MediaAsset) -> BinaryIO:
        return self.storage.open(asset)

    def signed_url(self, asset: MediaAsset, *, expires_in: int | None = None) -> str:
        return self.storage.signed_url(asset, expires_in=expires_in)

    @transaction.atomic
    def get(self, media_id) -> MediaAsset:
        return MediaAsset.objects.get(pk=media_id)
