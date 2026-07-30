"""Exporter base + registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import BinaryIO, ClassVar

from turing.domain.exceptions import ValidationError
from turing.services.export.document import ExportDocument


class BaseExporter(ABC):
    """Pluggable format renderer. Subclasses register via ``ExportRegistry``."""

    format_code: ClassVar[str] = ""
    content_type: ClassVar[str] = "application/octet-stream"
    file_extension: ClassVar[str] = "bin"
    label: ClassVar[str] = ""

    @abstractmethod
    def write(self, document: ExportDocument, output: BinaryIO) -> None:
        """Write the full document into ``output``."""

    def iter_chunks(
        self,
        document: ExportDocument,
        *,
        chunk_size: int = 64 * 1024,
    ) -> Iterator[bytes]:
        """
        Generate the document into a spooled temp file and yield chunks.

        Keeps peak memory bounded for large transcripts.
        """
        from tempfile import SpooledTemporaryFile

        with SpooledTemporaryFile(max_size=2 * 1024 * 1024, mode="w+b") as spool:
            self.write(document, spool)
            spool.seek(0)
            while True:
                chunk = spool.read(chunk_size)
                if not chunk:
                    break
                yield chunk


class ExportRegistry:
    """Format-code → exporter class registry."""

    _exporters: dict[str, type[BaseExporter]] = {}

    @classmethod
    def register(cls, exporter_cls: type[BaseExporter]) -> type[BaseExporter]:
        code = (exporter_cls.format_code or "").strip().lower()
        if not code:
            raise ValidationError("Exporter must define format_code.")
        cls._exporters[code] = exporter_cls
        return exporter_cls

    @classmethod
    def get(cls, format_code: str) -> BaseExporter:
        code = (format_code or "").strip().lower()
        exporter_cls = cls._exporters.get(code)
        if exporter_cls is None:
            supported = ", ".join(sorted(cls._exporters)) or "(none)"
            raise ValidationError(
                f"Unsupported export format '{format_code}'. Supported: {supported}."
            )
        return exporter_cls()

    @classmethod
    def supported_formats(cls) -> list[str]:
        return sorted(cls._exporters)
