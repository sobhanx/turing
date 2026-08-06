"""Best-effort audio metadata extraction (non-blocking for transcription)."""

from __future__ import annotations

import io
import logging
import wave
from dataclasses import dataclass
from typing import BinaryIO

logger = logging.getLogger(__name__)


@dataclass
class AudioMetadata:
    duration_ms: int | None = None
    sample_rate_hz: int | None = None
    channels: int | None = None
    audio_format: str = ""
    audio_codec: str = ""


def extract_audio_metadata(
    data: bytes,
    *,
    filename: str = "",
    content_type: str = "",
) -> AudioMetadata:
    """
    Extract duration / rate / channels / format when possible.

    Never raises — returns empty metadata on failure.
    """
    if not data:
        return AudioMetadata()

    ext = (filename.rsplit(".", 1)[-1].lower() if "." in filename else "") or ""
    try:
        if ext == "wav" or content_type in {"audio/wav", "audio/x-wav", "audio/wave"}:
            meta = _from_wave(data)
            if meta.audio_format or meta.duration_ms is not None:
                return meta
        mutagen_meta = _from_mutagen(data, filename=filename)
        if mutagen_meta.duration_ms is not None or mutagen_meta.sample_rate_hz:
            if not mutagen_meta.audio_format and ext:
                mutagen_meta.audio_format = ext
            return mutagen_meta
        if ext == "wav":
            return _from_wave(data)
    except Exception:  # noqa: BLE001
        logger.exception("Unexpected error during audio metadata extraction")
    return AudioMetadata(audio_format=ext)


def _from_wave(data: bytes) -> AudioMetadata:
    try:
        with wave.open(io.BytesIO(data), "rb") as handle:
            rate = handle.getframerate()
            channels = handle.getnchannels()
            frames = handle.getnframes()
            duration_ms = int(frames * 1000 / rate) if rate else None
            sampwidth = handle.getsampwidth()
            return AudioMetadata(
                duration_ms=duration_ms,
                sample_rate_hz=rate,
                channels=channels,
                audio_format="wav",
                audio_codec=f"pcm_{sampwidth * 8}bit",
            )
    except Exception as exc:  # noqa: BLE001
        logger.info("WAV metadata extraction failed: %s", exc)
        return AudioMetadata(audio_format="wav")


def _from_mutagen(data: bytes, *, filename: str = "") -> AudioMetadata:
    try:
        from mutagen import File as MutagenFile
    except ImportError:
        logger.debug("mutagen not installed; skipping advanced metadata extraction")
        return AudioMetadata()

    try:
        audio = MutagenFile(io.BytesIO(data), filename=filename or None)
        if audio is None:
            return AudioMetadata()
        info = getattr(audio, "info", None)
        if info is None:
            return AudioMetadata()

        duration_ms = None
        length = getattr(info, "length", None)
        if length is not None:
            duration_ms = int(float(length) * 1000)

        sample_rate = getattr(info, "sample_rate", None) or getattr(info, "samplerate", None)
        channels = getattr(info, "channels", None)
        codec = type(info).__name__.replace("Info", "").lower() or ""
        fmt = ""
        mime = getattr(audio, "mime", None)
        if mime:
            fmt = str(mime[0]).split("/")[-1]
        elif filename and "." in filename:
            fmt = filename.rsplit(".", 1)[-1].lower()

        return AudioMetadata(
            duration_ms=duration_ms,
            sample_rate_hz=int(sample_rate) if sample_rate else None,
            channels=int(channels) if channels else None,
            audio_format=fmt,
            audio_codec=codec,
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("mutagen metadata extraction failed: %s", exc)
        return AudioMetadata()


def extract_audio_metadata_from_path(
    path: str,
    *,
    filename: str = "",
    content_type: str = "",
) -> AudioMetadata:
    """
    Extract metadata from a filesystem path without loading the whole file into RAM.

    Never raises — returns empty metadata on failure.
    """
    name = filename or path.rsplit("/", 1)[-1]
    ext = (name.rsplit(".", 1)[-1].lower() if "." in name else "") or ""
    try:
        if ext == "wav" or content_type in {"audio/wav", "audio/x-wav", "audio/wave"}:
            meta = _from_wave_path(path)
            if meta.duration_ms is not None or meta.sample_rate_hz:
                return meta
        mutagen_meta = _from_mutagen_path(path, filename=name)
        if mutagen_meta.duration_ms is not None or mutagen_meta.sample_rate_hz:
            if not mutagen_meta.audio_format and ext:
                mutagen_meta.audio_format = ext
            return mutagen_meta
        if ext == "wav":
            return _from_wave_path(path)
    except Exception:  # noqa: BLE001
        logger.exception("Unexpected error during path metadata extraction")
    return AudioMetadata(audio_format=ext)


def _from_wave_path(path: str) -> AudioMetadata:
    try:
        with wave.open(path, "rb") as handle:
            rate = handle.getframerate()
            channels = handle.getnchannels()
            frames = handle.getnframes()
            duration_ms = int(frames * 1000 / rate) if rate else None
            sampwidth = handle.getsampwidth()
            return AudioMetadata(
                duration_ms=duration_ms,
                sample_rate_hz=rate,
                channels=channels,
                audio_format="wav",
                audio_codec=f"pcm_{sampwidth * 8}bit",
            )
    except Exception as exc:  # noqa: BLE001
        logger.info("WAV path metadata extraction failed: %s", exc)
        return AudioMetadata(audio_format="wav")


def _from_mutagen_path(path: str, *, filename: str = "") -> AudioMetadata:
    try:
        from mutagen import File as MutagenFile
    except ImportError:
        return AudioMetadata()
    try:
        audio = MutagenFile(path)
        if audio is None:
            return AudioMetadata()
        info = getattr(audio, "info", None)
        if info is None:
            return AudioMetadata()
        duration_ms = None
        length = getattr(info, "length", None)
        if length is not None:
            duration_ms = int(float(length) * 1000)
        sample_rate = getattr(info, "sample_rate", None) or getattr(info, "samplerate", None)
        channels = getattr(info, "channels", None)
        codec = type(info).__name__.replace("Info", "").lower() or ""
        fmt = ""
        mime = getattr(audio, "mime", None)
        if mime:
            fmt = str(mime[0]).split("/")[-1]
        elif filename and "." in filename:
            fmt = filename.rsplit(".", 1)[-1].lower()
        return AudioMetadata(
            duration_ms=duration_ms,
            sample_rate_hz=int(sample_rate) if sample_rate else None,
            channels=int(channels) if channels else None,
            audio_format=fmt,
            audio_codec=codec,
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("mutagen path metadata extraction failed: %s", exc)
        return AudioMetadata()


def extract_from_stream(stream: BinaryIO, *, filename: str = "", content_type: str = "") -> AudioMetadata:
    position = None
    try:
        position = stream.tell()
    except Exception:
        position = None
    data = stream.read()
    if position is not None:
        try:
            stream.seek(position)
        except Exception:
            pass
    return extract_audio_metadata(data, filename=filename, content_type=content_type)
