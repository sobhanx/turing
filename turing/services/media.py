from __future__ import annotations

import hashlib
import mimetypes
from typing import BinaryIO

from django.contrib.auth.models import AbstractBaseUser
from django.core.files.base import ContentFile
from django.db import transaction

from turing.conf import get_turing_settings
from turing.domain.enums import SourceType, StorageBackend, UseCase
from turing.domain.exceptions import ValidationError
from turing.models import MediaAsset


class MediaService:
    """Register and manage media inputs for the speech engine."""

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
        raw = uploaded_file.read()
        if not raw:
            raise ValidationError("Uploaded file is empty.")
        if len(raw) > settings.max_upload_bytes:
            raise ValidationError(
                f"File exceeds max upload size of {settings.max_upload_bytes} bytes."
            )

        checksum = hashlib.sha256(raw).hexdigest()
        guessed_type = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"

        asset = MediaAsset(
            source_type=SourceType.UPLOAD,
            use_case=use_case,
            storage_backend=settings.storage_backend or StorageBackend.LOCAL,
            original_filename=filename,
            content_type=guessed_type,
            byte_size=len(raw),
            checksum=checksum,
            uploaded_by=uploaded_by,
            tenant_key=tenant_key or "",
            metadata=metadata or {},
        )
        asset.file.save(filename, ContentFile(raw), save=False)
        asset.object_key = asset.file.name
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

    @transaction.atomic
    def get(self, media_id) -> MediaAsset:
        return MediaAsset.objects.get(pk=media_id)
