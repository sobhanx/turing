"""Stream uploads to disk to avoid holding large media in memory."""

from __future__ import annotations

import hashlib
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from typing import BinaryIO, Iterator

from turing.domain.exceptions import ValidationError

CHUNK_SIZE = 1024 * 1024  # 1 MiB


@dataclass
class SpooledUpload:
    path: str
    size: int
    checksum: str

    def open(self, mode: str = "rb") -> BinaryIO:
        return open(self.path, mode)  # noqa: SIM115


@contextmanager
def spool_upload(
    source: BinaryIO | bytes,
    *,
    max_bytes: int,
    chunk_size: int = CHUNK_SIZE,
) -> Iterator[SpooledUpload]:
    """
    Copy ``source`` to a temp file while hashing and enforcing ``max_bytes``.

    Yields a ``SpooledUpload``; the temp file is deleted on exit.
    """
    fd, path = tempfile.mkstemp(prefix="turing-upload-", suffix=".bin")
    size = 0
    hasher = hashlib.sha256()
    try:
        with os.fdopen(fd, "wb") as out:
            if isinstance(source, (bytes, bytearray, memoryview)):
                data = bytes(source)
                size = len(data)
                if size > max_bytes:
                    raise ValidationError(
                        f"Upload exceeds max upload size ({max_bytes} bytes)."
                    )
                hasher.update(data)
                out.write(data)
            else:
                while True:
                    chunk = source.read(chunk_size)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValidationError(
                            f"Upload exceeds max upload size ({max_bytes} bytes)."
                        )
                    hasher.update(chunk)
                    out.write(chunk)
            out.flush()

        yield SpooledUpload(path=path, size=size, checksum=hasher.hexdigest())
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
