from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO

from django.conf import settings
from django.core.files.storage import default_storage


class StorageGateway(ABC):
    """Port for media/object storage used by Turing services."""

    @abstractmethod
    def save(self, key: str, content: BinaryIO) -> str:
        ...

    @abstractmethod
    def open(self, key: str) -> BinaryIO:
        ...

    @abstractmethod
    def exists(self, key: str) -> bool:
        ...

    @abstractmethod
    def url(self, key: str) -> str:
        ...

    @abstractmethod
    def read_bytes(self, key: str) -> bytes:
        ...


class DjangoStorageGateway(StorageGateway):
    """Uses Django's default_storage (local / S3 / Azure / GCS via django-storages)."""

    def save(self, key: str, content: BinaryIO) -> str:
        return default_storage.save(key, content)

    def open(self, key: str) -> BinaryIO:
        return default_storage.open(key, "rb")

    def exists(self, key: str) -> bool:
        return default_storage.exists(key)

    def url(self, key: str) -> str:
        return default_storage.url(key)

    def read_bytes(self, key: str) -> bytes:
        with self.open(key) as handle:
            return handle.read()


def get_storage_gateway() -> StorageGateway:
    return DjangoStorageGateway()


def absolute_media_path(file_field_name: str) -> Path | None:
    """Resolve a local filesystem path when using local storage."""
    if not file_field_name:
        return None
    media_root = Path(settings.MEDIA_ROOT)
    path = media_root / file_field_name
    return path if path.exists() else None
