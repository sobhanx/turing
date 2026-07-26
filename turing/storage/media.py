"""Media storage service — MediaAsset I/O without filesystem coupling."""

from __future__ import annotations

import logging
from typing import BinaryIO

from django.core.files.base import ContentFile
from django.utils.text import get_valid_filename

from turing.models import MediaAsset
from turing.storage.backends import StorageGateway, get_storage_gateway

logger = logging.getLogger(__name__)


class MediaStorageService:
    """
    High-level media storage operations for MediaAsset.

    Keeps services/adapters off Django local paths so S3/Azure/GCS can
    be introduced later by swapping the StorageGateway only.
    """

    def __init__(self, gateway: StorageGateway | None = None) -> None:
        self.gateway = gateway or get_storage_gateway()

    @property
    def backend_code(self) -> str:
        return self.gateway.backend_code

    def save_upload(
        self,
        *,
        filename: str,
        content: bytes | BinaryIO,
        key_prefix: str = "turing/media",
    ) -> str:
        safe_name = get_valid_filename(filename) or "audio.bin"
        from django.utils import timezone

        stamp = timezone.now()
        key = f"{key_prefix}/{stamp:%Y/%m}/{safe_name}"
        if hasattr(content, "read"):
            data = content.read()
        else:
            data = content
        return self.gateway.save(key, ContentFile(data))

    def read_bytes(self, asset: MediaAsset) -> bytes:
        key = self._resolve_key(asset)
        if not key:
            raise FileNotFoundError(f"MediaAsset {asset.id} has no stored object key.")
        return self.gateway.read_bytes(key)

    def open(self, asset: MediaAsset) -> BinaryIO:
        key = self._resolve_key(asset)
        if not key:
            raise FileNotFoundError(f"MediaAsset {asset.id} has no stored object key.")
        return self.gateway.open(key)

    def exists(self, asset: MediaAsset) -> bool:
        key = self._resolve_key(asset)
        return bool(key) and self.gateway.exists(key)

    def delete(self, asset: MediaAsset) -> None:
        key = self._resolve_key(asset)
        if key:
            self.gateway.delete(key)

    def url(self, asset: MediaAsset) -> str:
        key = self._resolve_key(asset)
        if not key:
            return ""
        return self.gateway.url(key)

    def _resolve_key(self, asset: MediaAsset) -> str:
        if asset.object_key:
            return asset.object_key
        if asset.file and asset.file.name:
            return asset.file.name
        return ""
