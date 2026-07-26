from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import BinaryIO

from django.core.files import File
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from turing.conf import get_turing_settings
from turing.domain.enums import StorageBackend

logger = logging.getLogger(__name__)


class StorageGateway(ABC):
    """
    Port for media/object storage.

    Business logic depends on this interface — not local filesystem paths.
    Local, S3, Azure, and GCS can plug in behind the same contract.
    """

    backend_code: str = StorageBackend.LOCAL

    @abstractmethod
    def save(self, key: str, content: BinaryIO | File | bytes, *, content_type: str = "") -> str:
        ...

    @abstractmethod
    def open(self, key: str) -> BinaryIO:
        ...

    @abstractmethod
    def exists(self, key: str) -> bool:
        ...

    @abstractmethod
    def delete(self, key: str) -> None:
        ...

    @abstractmethod
    def url(self, key: str) -> str:
        ...

    def signed_url(self, key: str, *, expires_in: int | None = None) -> str:
        """
        Return a time-limited URL for private objects.

        Default implementation falls back to ``url`` (suitable for local public media).
        S3-compatible backends should override or rely on django-storages querystring auth.
        """
        return self.url(key)

    def read_bytes(self, key: str) -> bytes:
        """Load entire object into memory — prefer ``open`` / signed URLs for large media."""
        with self.open(key) as handle:
            return handle.read()

    def supports_remote_fetch(self) -> bool:
        """True when signed/public HTTP URLs can be fetched by external providers (e.g. STT)."""
        return self.backend_code in {
            StorageBackend.S3,
            StorageBackend.AZURE,
            StorageBackend.GCS,
        }


class DjangoStorageGateway(StorageGateway):
    """
    Django ``default_storage`` adapter.

    Local filesystem by default; S3/Azure/GCS via django-storages when
    ``STORAGES['default']`` is configured (see ``config.settings.storage``).
    """

    def __init__(self, backend_code: str | None = None) -> None:
        settings = get_turing_settings()
        self.backend_code = backend_code or settings.storage_backend or StorageBackend.LOCAL

    def save(self, key: str, content: BinaryIO | File | bytes, *, content_type: str = "") -> str:
        file_obj = self._as_django_file(content, key=key, content_type=content_type)
        return default_storage.save(key, file_obj)

    def open(self, key: str) -> BinaryIO:
        return default_storage.open(key, "rb")

    def exists(self, key: str) -> bool:
        return default_storage.exists(key)

    def delete(self, key: str) -> None:
        if key and default_storage.exists(key):
            default_storage.delete(key)

    def url(self, key: str) -> str:
        return default_storage.url(key)

    def signed_url(self, key: str, *, expires_in: int | None = None) -> str:
        settings = get_turing_settings()
        ttl = (
            expires_in
            if expires_in is not None
            else getattr(settings, "signed_url_ttl_seconds", 3600)
        )
        # django-storages S3Boto3Storage.url(name, expire=...)
        try:
            return default_storage.url(key, expire=int(ttl))
        except TypeError:
            return default_storage.url(key)

    def _as_django_file(
        self,
        content: BinaryIO | File | bytes,
        *,
        key: str,
        content_type: str,
    ) -> File:
        name = key.rsplit("/", 1)[-1] or "audio.bin"
        if isinstance(content, bytes):
            file_obj: File = ContentFile(content, name=name)
        elif isinstance(content, File):
            file_obj = content
        else:
            # Stream file-like without buffering entire body into memory.
            if hasattr(content, "seek"):
                try:
                    content.seek(0)
                except Exception:  # noqa: BLE001
                    pass
            file_obj = File(content, name=name)

        if content_type:
            # Used by django-storages to set ContentType / MIME on object storage.
            try:
                file_obj.content_type = content_type  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                logger.debug("Could not set content_type on storage file object")
        return file_obj


def get_storage_gateway(*, backend_code: str | None = None) -> StorageGateway:
    """Factory for the active storage gateway (local by default)."""
    return DjangoStorageGateway(backend_code=backend_code)
