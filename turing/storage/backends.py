from __future__ import annotations

from abc import ABC, abstractmethod
from typing import BinaryIO

from django.core.files.base import ContentFile, File
from django.core.files.storage import default_storage

from turing.conf import get_turing_settings
from turing.domain.enums import StorageBackend


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

    def read_bytes(self, key: str) -> bytes:
        with self.open(key) as handle:
            return handle.read()


class DjangoStorageGateway(StorageGateway):
    """
    Django ``default_storage`` adapter.

    Works with local filesystem today; django-storages can swap the
    underlying backend to S3/Azure/GCS without changing callers.
    """

    def __init__(self, backend_code: str | None = None) -> None:
        settings = get_turing_settings()
        self.backend_code = backend_code or settings.storage_backend or StorageBackend.LOCAL

    def save(self, key: str, content: BinaryIO | File | bytes, *, content_type: str = "") -> str:
        if isinstance(content, bytes):
            content = ContentFile(content)
        return default_storage.save(key, content)

    def open(self, key: str) -> BinaryIO:
        return default_storage.open(key, "rb")

    def exists(self, key: str) -> bool:
        return default_storage.exists(key)

    def delete(self, key: str) -> None:
        if key and default_storage.exists(key):
            default_storage.delete(key)

    def url(self, key: str) -> str:
        return default_storage.url(key)


def get_storage_gateway(*, backend_code: str | None = None) -> StorageGateway:
    """Factory for the active storage gateway (local by default)."""
    return DjangoStorageGateway(backend_code=backend_code)
